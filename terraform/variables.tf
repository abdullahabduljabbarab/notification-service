variable "project_id" {
  description = "GCP project ID. The notification service shares the ledger's project."
  type        = string
  default     = "ledger-api-507618"
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "europe-west2"
}

variable "sql_instance_name" {
  description = "Name of the ledger's Cloud SQL instance the notification service takes its database on"
  type        = string
  default     = "ledger-db"
}

variable "payment_events_topic" {
  description = "The orchestrator's topic the notification service consumes payment events from"
  type        = string
  default     = "payment-events"
}

variable "risk_events_topic" {
  description = "The risk engine's topic the notification service consumes risk decisions from"
  type        = string
  default     = "risk-events"
}

variable "notify_db_password" {
  description = "Password for the notification service's database user. Stored in Secret Manager as part of the connection string and injected into Cloud Run, never set as a plaintext env var."
  type        = string
  sensitive   = true
}

variable "wif_pool_id" {
  description = "The shared GitHub Actions Workload Identity pool, owned by platform-infrastructure and referenced here"
  type        = string
  default     = "github-actions"
}

variable "github_owner" {
  description = "GitHub owner allowed to deploy via Workload Identity Federation"
  type        = string
  default     = "abdullahabduljabbarab"
}

variable "github_repo" {
  description = "GitHub repository allowed to impersonate the deploy service account"
  type        = string
  default     = "notification-service"
}
