resource "aws_s3_object" "function_zip" {
  bucket = var.artifacts_bucket
  key    = "artifacts/${local.name_prefix}/function.zip"
  source = var.function_zip_path
  etag   = filemd5(var.function_zip_path)
  tags   = local.common_tags
}

resource "aws_s3_object" "layer_zip" {
  bucket = var.artifacts_bucket
  key    = "artifacts/${local.name_prefix}/layer.zip"
  source = var.layer_zip_path
  etag   = filemd5(var.layer_zip_path)
  tags   = local.common_tags
}
