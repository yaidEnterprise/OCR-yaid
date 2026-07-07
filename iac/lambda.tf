resource "aws_lambda_layer_version" "tesseract" {
  layer_name               = "${local.name_prefix}-tesseract"
  s3_bucket                = var.artifacts_bucket
  s3_key                   = aws_s3_object.layer_zip.key
  compatible_architectures = ["x86_64"]
  compatible_runtimes      = ["python3.12"]
  source_code_hash         = filebase64sha256(var.layer_zip_path)
}

resource "aws_lambda_function" "ocr" {
  function_name = "${local.name_prefix}-ocr"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "main.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]

  s3_bucket        = var.artifacts_bucket
  s3_key           = aws_s3_object.function_zip.key
  source_code_hash = filebase64sha256(var.function_zip_path)

  layers      = [aws_lambda_layer_version.tesseract.arn]
  memory_size = 2048
  timeout     = 30

  environment {
    variables = {
      TESSERACT_CMD   = "/opt/bin/tesseract"
      TESSDATA_PREFIX = "/opt/tesseract/share/tessdata"
      LD_LIBRARY_PATH = "/opt/lib"
      API_KEY         = var.api_key
    }
  }

  tags = local.common_tags

  depends_on = [aws_iam_role_policy_attachment.lambda_basic_execution]
}
