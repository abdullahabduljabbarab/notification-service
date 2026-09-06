output "cloud_run_url" {
  description = "Live notification service URL"
  value       = google_cloud_run_v2_service.notification_service.uri
}

output "artifact_registry" {
  description = "Docker image registry path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/notification-service"
}

output "payment_events_subscription" {
  description = "Push subscription feeding payment events into the service"
  value       = google_pubsub_subscription.payment_events_to_notification.name
}

output "risk_events_subscription" {
  description = "Push subscription feeding risk decisions into the service"
  value       = google_pubsub_subscription.risk_events_to_notification.name
}

output "dead_letter_topic" {
  description = "Transport dead-letter topic for unprocessable events"
  value       = google_pubsub_topic.dead_letter.id
}

output "notify_database" {
  description = "Notification service database on the shared Cloud SQL instance"
  value       = google_sql_database.notify.name
}
