output "api_endpoint" {
  description = "URL pública da API"
  value       = aws_apigatewayv2_api.ocr.api_endpoint
}

output "lambda_function_name" {
  description = "Nome da função Lambda implantada"
  value       = aws_lambda_function.ocr.function_name
}

output "tesseract_layer_arn" {
  description = "ARN da versão do layer do Tesseract"
  value       = aws_lambda_layer_version.tesseract.arn
}
