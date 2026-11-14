output "cluster_name" {
  value = module.eks.cluster_name
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "database_host" {
  value = aws_db_instance.postgres.address
}

output "ecr_repositories" {
  value = { for name, repo in aws_ecr_repository.repo : name => repo.repository_url }
}

output "rabbitmq_endpoint" {
  value = aws_mq_broker.events.instances[0].endpoints[0]
}

output "media_file_system_id" {
  value = aws_efs_file_system.media.id
}
