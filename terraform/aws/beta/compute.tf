resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  instance_id = aws_instance.host.id
  volume_id   = aws_ebs_volume.data.id
}

resource "aws_ebs_volume" "data" {
  availability_zone    = "ap-northeast-1a"
  encrypted            = true
  iops                 = 3000
  kms_key_id           = "arn:aws:kms:ap-northeast-1:535562976288:key/bb093c1f-71bd-47bc-8d17-69b68f2445af"
  multi_attach_enabled = false
  size                 = 30
  tags = {
    Name    = "fuminosuke-beta-data"
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  throughput = 125
  type       = "gp3"
  lifecycle { prevent_destroy = true }
}

resource "aws_instance" "host" {
  ami                                  = "ami-0739e5f3b35c608e5"
  availability_zone                    = "ap-northeast-1a"
  disable_api_stop                     = false
  disable_api_termination              = false
  ebs_optimized                        = false
  force_destroy                        = false
  get_password_data                    = false
  hibernation                          = false
  iam_instance_profile                 = aws_iam_instance_profile.host.name
  instance_initiated_shutdown_behavior = "stop"
  instance_type                        = "t3a.medium"
  monitoring                           = false
  secondary_private_ips                = []
  source_dest_check                    = true
  subnet_id                            = aws_subnet.main.id
  tags = {
    Name    = "fuminosuke-beta"
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  tenancy                = "default"
  user_data              = templatefile("${path.module}/cloud-init.sh.tftpl", { data_volume_id = aws_ebs_volume.data.id })
  vpc_security_group_ids = [aws_security_group.web.id]
  capacity_reservation_specification {
    capacity_reservation_preference = "open"
  }
  cpu_options {
    core_count       = 1
    threads_per_core = 2
  }
  credit_specification {
    cpu_credits = "standard"
  }
  enclave_options {
    enabled = false
  }
  maintenance_options {
    auto_recovery = "default"
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 2
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }
  private_dns_name_options {
    enable_resource_name_dns_a_record    = false
    enable_resource_name_dns_aaaa_record = false
    hostname_type                        = "ip-name"
  }
  root_block_device {
    delete_on_termination = true
    encrypted             = true
    iops                  = 3000
    kms_key_id            = "arn:aws:kms:ap-northeast-1:535562976288:key/bb093c1f-71bd-47bc-8d17-69b68f2445af"
    tags                  = {}
    throughput            = 125
    volume_size           = 20
    volume_type           = "gp3"
  }
  lifecycle { prevent_destroy = true }
  depends_on = [aws_route.internet]
}
