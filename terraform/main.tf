resource "aws_security_group" "payments_sg" {
  name        = "payments-sg"
  description = "ChangeGuard demo security group"

  ingress {
    description = "Internal SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "Internal Postgres access"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  tags = {
    Name = "payments-sg"
  }
}

resource "aws_ecs_service" "payments_api" {
  name = "payments-api"

  cluster         = "arn:aws:ecs:us-east-1:000000000000:cluster/changeguard-demo"
  task_definition = "arn:aws:ecs:us-east-1:000000000000:task-definition/payments-api:1"

  desired_count       = 3
  scheduling_strategy = "REPLICA"
}

resource "aws_db_instance" "payments_db" {
  identifier = "payments-db"

  engine            = "postgres"
  engine_version    = "17"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "payments"
  username = "changeguard"
  password = "changeguard-demo-password"

  deletion_protection = true
  skip_final_snapshot = true
}
