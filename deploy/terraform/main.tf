# Інфраструктура Kairo в AWS: VPC, кластер EKS, PostgreSQL 16 (RDS, з pgvector),
# реєстри образів ECR. Застосунок установлюється в кластер Helm-чартом ../helm/kairo.
#
#   terraform init && terraform apply -var="db_password=..."
#
# Конфігурацію підготовлено для розгортання; у межах роботи навантажувальні
# експерименти виконано на локальному стенді Docker Compose.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "kairo", ManagedBy = "terraform" }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "kairo"
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name            = local.name
  cidr            = "10.20.0.0/16"
  azs             = local.azs
  private_subnets = ["10.20.1.0/24", "10.20.2.0/24", "10.20.3.0/24"]
  public_subnets  = ["10.20.101.0/24", "10.20.102.0/24", "10.20.103.0/24"]

  enable_nat_gateway = true
  single_nat_gateway = true # дешевше; для production — по NAT на зону

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = local.name
  cluster_version = "1.31"

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    general = {
      instance_types = [var.node_instance_type]
      min_size       = 2
      max_size       = 6
      desired_size   = 3
    }
  }
}

resource "aws_db_subnet_group" "db" {
  name       = "${local.name}-db"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db" {
  name   = "${local.name}-db"
  vpc_id = module.vpc.vpc_id

  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# PostgreSQL 16 на RDS підтримує розширення pgvector (CREATE EXTENSION vector)
resource "aws_db_instance" "postgres" {
  identifier             = "${local.name}-pg"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.db_instance_class
  allocated_storage      = 50
  max_allocated_storage  = 200
  db_name                = "kairo"
  username               = "kairo"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  multi_az               = var.db_multi_az
  storage_encrypted      = true
  backup_retention_period = 7
  skip_final_snapshot    = true
}

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["kairo-backend", "kairo-frontend"])
  name                 = each.value
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}
