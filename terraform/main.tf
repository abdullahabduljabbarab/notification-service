terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {}

# The Cloud SQL instance is owned by the ledger. The notification service takes
# its own database and user on it rather than standing up a second server.
data "google_sql_database_instance" "ledger_db" {
  name = var.sql_instance_name
}

# The two topics the notification service consumes, both owned elsewhere: the
# orchestrator's payment events and the risk engine's decisions. Referenced
# here, never created here. The service publishes nothing (strict sink).
data "google_pubsub_topic" "payment_events" {
  name = var.payment_events_topic
}

data "google_pubsub_topic" "risk_events" {
  name = var.risk_events_topic
}

resource "google_sql_database" "notify" {
  name     = "notify"
  instance = data.google_sql_database_instance.ledger_db.name
}

resource "google_sql_user" "notify" {
  name     = "notify"
  instance = data.google_sql_database_instance.ledger_db.name
  password = var.notify_db_password
}

resource "google_artifact_registry_repository" "notification_service" {
  location      = var.region
  repository_id = "notification-service"
  format        = "DOCKER"
}

# The full connection string is one secret, so Cloud Run never sees a plaintext
# DATABASE_URL. The socket path points at the shared Cloud SQL instance.
resource "google_secret_manager_secret" "notify_database_url" {
  secret_id = "notify-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "notify_database_url" {
  secret      = google_secret_manager_secret.notify_database_url.id
  secret_data = "postgresql://${google_sql_user.notify.name}:${var.notify_db_password}@/${google_sql_database.notify.name}?host=/cloudsql/${data.google_sql_database_instance.ledger_db.connection_name}"
}

resource "google_service_account" "cloud_run" {
  account_id   = "notification-runner"
  display_name = "Notification Service Cloud Run"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_database_url" {
  secret_id = google_secret_manager_secret.notify_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}

resource "google_cloud_run_v2_service" "notification_service" {
  name     = "notification-service"
  location = var.region

  template {
    service_account = google_service_account.cloud_run.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/notification-service/notification-service:latest"

      ports {
        container_port = 8080
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.notify_database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }

      env {
        name  = "PUBSUB_PUSH_SA"
        value = google_service_account.pubsub_push.email
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [data.google_sql_database_instance.ledger_db.connection_name]
      }
    }
  }
}

# The read API and health check are public; the ingest endpoint is protected at
# the application layer by verifying the push OIDC token, so public invoke is
# safe and matches the risk engine's model.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.notification_service.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Dead-letter for events the push endpoint cannot process at all (the service
# failing, not a channel exhausting its retries, which is handled in-band). A
# poison message is retried a bounded number of times and then parked.
resource "google_pubsub_topic" "dead_letter" {
  name = "notification-events-deadletter"
}

# A dedicated identity for the push subscriptions. Pub/Sub mints an OIDC token as
# this account and attaches it to each push; the consumer endpoint verifies it,
# so only authenticated Pub/Sub deliveries reach the consumer.
resource "google_service_account" "pubsub_push" {
  account_id   = "notify-pubsub-push"
  display_name = "Notification Service Pub/Sub Push"
}

# Pub/Sub's service agent must be allowed to mint tokens as the push identity.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# Push subscription on the orchestrator's payment-events topic.
resource "google_pubsub_subscription" "payment_events_to_notification" {
  name  = "payment-events-to-notification"
  topic = data.google_pubsub_topic.payment_events.id

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.notification_service.uri}/events/pubsub"

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
    }
  }

  ack_deadline_seconds = 20

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# Push subscription on the risk engine's risk-events topic.
resource "google_pubsub_subscription" "risk_events_to_notification" {
  name  = "risk-events-to-notification"
  topic = data.google_pubsub_topic.risk_events.id

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.notification_service.uri}/events/pubsub"

    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
    }
  }

  ack_deadline_seconds = 20

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# Dead-lettering requires the Pub/Sub service agent to publish to the dead-letter
# topic and to acknowledge on each subscription.
resource "google_pubsub_topic_iam_member" "dead_letter_publish" {
  topic  = google_pubsub_topic.dead_letter.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "payment_dead_letter_subscribe" {
  subscription = google_pubsub_subscription.payment_events_to_notification.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "risk_dead_letter_subscribe" {
  subscription = google_pubsub_subscription.risk_events_to_notification.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# Keyless CI deploy via Workload Identity Federation. The pool and provider are
# shared across the ABS services (created by the first service to adopt this
# model) and are referenced here, not recreated; they move to
# platform-infrastructure when it is consolidated. This service contributes only
# its own least-privilege deploy account and the binding that lets its own
# repository impersonate it.
data "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = var.wif_pool_id
}

resource "google_service_account" "deploy" {
  account_id   = "notification-service-deploy"
  display_name = "Notification Service Deploy"
}

resource "google_project_iam_member" "deploy_roles" {
  for_each = toset([
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# Only the notification-service repository may impersonate the deploy account.
resource "google_service_account_iam_member" "deploy_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${data.google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}
