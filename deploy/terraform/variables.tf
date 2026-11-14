variable "region" {
  description = "Регіон AWS"
  type        = string
  default     = "eu-central-1"
}

variable "node_instance_type" {
  description = "Тип вузлів EKS"
  type        = string
  default     = "t3.large"
}

variable "db_instance_class" {
  description = "Клас інстансу RDS PostgreSQL"
  type        = string
  default     = "db.t4g.medium"
}

variable "db_multi_az" {
  description = "Резервна копія БД в іншій зоні доступності"
  type        = bool
  default     = true
}

variable "db_password" {
  description = "Пароль користувача kairo у PostgreSQL"
  type        = string
  sensitive   = true
}

variable "mq_instance_type" {
  description = "Тип інстансу Amazon MQ (RabbitMQ)"
  type        = string
  default     = "mq.t3.micro"
}

variable "mq_cluster" {
  description = "Кластер із трьох вузлів у різних зонах замість одного інстансу"
  type        = bool
  default     = false
}

variable "mq_password" {
  description = "Пароль користувача RabbitMQ (щонайменше 12 символів)"
  type        = string
  sensitive   = true
}
