resource "aws_route_table" "main" {
  propagating_vgws = []
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  vpc_id = aws_vpc.main.id
}

resource "aws_route" "internet" {
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
  route_table_id         = aws_route_table.main.id
}

resource "aws_security_group" "web" {
  description = "HTTPS and redirect only; management through SSM"
  egress = [{
    cidr_blocks      = ["0.0.0.0/0"]
    description      = ""
    from_port        = 0
    ipv6_cidr_blocks = []
    prefix_list_ids  = []
    protocol         = "-1"
    security_groups  = []
    self             = false
    to_port          = 0
  }]
  ingress = [{
    cidr_blocks      = ["0.0.0.0/0"]
    description      = ""
    from_port        = 443
    ipv6_cidr_blocks = []
    prefix_list_ids  = []
    protocol         = "tcp"
    security_groups  = []
    self             = false
    to_port          = 443
    }, {
    cidr_blocks      = ["0.0.0.0/0"]
    description      = ""
    from_port        = 80
    ipv6_cidr_blocks = []
    prefix_list_ids  = []
    protocol         = "tcp"
    security_groups  = []
    self             = false
    to_port          = 80
  }]
  name = "fuminosuke-beta-WebSecurityGroup-kQcwwXFEau0U"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  vpc_id = aws_vpc.main.id
}

resource "aws_eip_association" "main" {
  allocation_id = aws_eip.main.id
  instance_id   = aws_instance.host.id
}

resource "aws_subnet" "main" {
  assign_ipv6_address_on_creation                = false
  availability_zone                              = "ap-northeast-1a"
  cidr_block                                     = "10.92.0.0/26"
  enable_dns64                                   = false
  enable_resource_name_dns_a_record_on_launch    = false
  enable_resource_name_dns_aaaa_record_on_launch = false
  ipv6_native                                    = false
  map_public_ip_on_launch                        = false
  private_dns_hostname_type_on_launch            = "ip-name"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  vpc_id = aws_vpc.main.id
}

resource "aws_eip" "main" {
  domain               = "vpc"
  network_border_group = "ap-northeast-1"
  public_ipv4_pool     = "amazon"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_internet_gateway" "main" {
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  vpc_id = aws_vpc.main.id
}

resource "aws_vpc" "main" {
  assign_generated_ipv6_cidr_block     = false
  cidr_block                           = "10.92.0.0/24"
  enable_dns_hostnames                 = true
  enable_dns_support                   = true
  enable_network_address_usage_metrics = false
  instance_tenancy                     = "default"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
}

resource "aws_route_table_association" "main" {
  subnet_id      = aws_subnet.main.id
  route_table_id = aws_route_table.main.id
}
