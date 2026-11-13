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
