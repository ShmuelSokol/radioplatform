# Studio Kol Bramah — Terraform Infrastructure
#
# This configuration provisions the infrastructure for the radio platform:
# - Vercel project for frontend
# - Railway project (managed externally — Railway has no official Terraform provider)
# - Supabase project (managed externally — no official provider)
#
# Usage:
#   cd infra/terraform
#   terraform init
#   terraform plan
#   terraform apply

terraform {
  required_version = ">= 1.5"

  required_providers {
    vercel = {
      source  = "vercel/vercel"
      version = "~> 1.0"
    }
  }
}

# ─── Variables ───

variable "vercel_api_token" {
  description = "Vercel API token"
  type        = string
  sensitive   = true
}

variable "vercel_team_id" {
  description = "Vercel team ID (optional for personal accounts)"
  type        = string
  default     = null
}

variable "github_repo" {
  description = "GitHub repository (org/repo)"
  type        = string
  default     = "ShmuelSokol/radioplatform"
}

variable "api_url" {
  description = "Backend API URL"
  type        = string
  default     = "https://studio-kolbramah-api-production.up.railway.app/api/v1"
}

variable "ws_url" {
  description = "WebSocket URL for real-time updates"
  type        = string
  default     = "wss://studio-kolbramah-api-production.up.railway.app"
}

# ─── Providers ───

provider "vercel" {
  api_token = var.vercel_api_token
  team      = var.vercel_team_id
}

# ─── Frontend (Vercel) ───

resource "vercel_project" "frontend" {
  name      = "studio-kolbramah-radio"
  framework = "vite"

  git_repository {
    type = "github"
    repo = var.github_repo
  }

  root_directory = "frontend"

  build_command    = "npm run build"
  output_directory = "dist"
}

resource "vercel_project_environment_variable" "api_url" {
  project_id = vercel_project.frontend.id
  key        = "VITE_API_URL"
  value      = var.api_url
  target     = ["production", "preview"]
}

resource "vercel_project_environment_variable" "ws_url" {
  project_id = vercel_project.frontend.id
  key        = "VITE_WS_URL"
  value      = var.ws_url
  target     = ["production", "preview"]
}

# ─── Outputs ───

output "frontend_url" {
  value = "https://${vercel_project.frontend.name}.vercel.app"
}

output "api_url" {
  value = var.api_url
}
