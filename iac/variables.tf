variable "region" {
  description = "Região AWS onde os recursos são provisionados"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefixo usado para nomear os recursos"
  type        = string
}

variable "stage" {
  description = "Estágio de deploy, igual ao nome da branch (ex.: prod)"
  type        = string
}

variable "api_key" {
  description = "Chave de API validada pelo FastAPI no header x-api-key"
  type        = string
  sensitive   = true
}

variable "artifacts_bucket" {
  description = "Bucket S3 (mesmo do backend) que hospeda os artefatos de build sob artifacts/"
  type        = string
}

variable "function_zip_path" {
  description = "Caminho local do function.zip a ser enviado ao S3"
  type        = string
  default     = "../build/function.zip"
}

variable "layer_zip_path" {
  description = "Caminho local do layer.zip a ser enviado ao S3"
  type        = string
  default     = "../build/layer.zip"
}

locals {
  name_prefix = "${var.project_name}-${var.stage}"

  common_tags = {
    Project   = var.project_name
    Stage     = var.stage
    ManagedBy = "terraform"
  }
}
