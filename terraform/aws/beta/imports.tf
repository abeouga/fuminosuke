# One-time adoption of the existing beta. These blocks are idempotent.
import {
  to = aws_vpc.main
  id = "vpc-0daffd24efe21abc3"
}

import {
  to = aws_internet_gateway.main
  id = "igw-0811180728f1d10de"
}

import {
  to = aws_subnet.main
  id = "subnet-0235f970a5a4bc5a7"
}

import {
  to = aws_route_table.main
  id = "rtb-06c0206d2ee613596"
}

import {
  to = aws_route_table_association.main
  id = "subnet-0235f970a5a4bc5a7/rtb-06c0206d2ee613596"
}

import {
  to = aws_route.internet
  id = "rtb-06c0206d2ee613596_0.0.0.0/0"
}

import {
  to = aws_security_group.web
  id = "sg-04fc97d6abef7b531"
}

import {
  to = aws_iam_role.host
  id = "fuminosuke-beta-InstanceRole-MadM8YzqJsrj"
}

import {
  to = aws_iam_role_policy.persistence
  id = "fuminosuke-beta-InstanceRole-MadM8YzqJsrj:BetaPersistence"
}

import {
  to = aws_iam_role_policy_attachment.ssm
  id = "fuminosuke-beta-InstanceRole-MadM8YzqJsrj/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

import {
  to = aws_iam_instance_profile.host
  id = "fuminosuke-beta-Profile-4CXkbIVmZaPc"
}

import {
  to = aws_secretsmanager_secret.openai
  id = "arn:aws:secretsmanager:ap-northeast-1:535562976288:secret:ApiSecret-bQAoakndLQlN-hFbrCO"
}

import {
  to = aws_ebs_volume.data
  id = "vol-0ce80d76706c22e0a"
}

import {
  to = aws_instance.host
  id = "i-0d1217ab2ab9d4fa3"
}

import {
  to = aws_volume_attachment.data
  id = "/dev/sdf:vol-0ce80d76706c22e0a:i-0d1217ab2ab9d4fa3"
}

import {
  to = aws_eip.main
  id = "eipalloc-0f3ac386052ebedaa"
}

import {
  to = aws_eip_association.main
  id = "eipassoc-0aec367d97126777f"
}

import {
  to = aws_cognito_user_pool.main
  id = "ap-northeast-1_tAAw9ha3X"
}

import {
  to = aws_cognito_user_pool_client.web
  id = "ap-northeast-1_tAAw9ha3X/s8s42dkh41qbotdbo3n9ng5au"
}

import {
  to = aws_cognito_user_pool_domain.main
  id = "fuminosuke-beta-535562976288"
}

import {
  to = aws_budgets_budget.monthly
  id = "535562976288:fuminosuke-beta-monthly"
}

import {
  to = aws_s3_bucket.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket_public_access_block.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket_ownership_controls.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket_policy.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

import {
  to = aws_s3_bucket_public_access_block.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

import {
  to = aws_s3_bucket_ownership_controls.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

import {
  to = aws_s3_bucket_policy.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

import {
  to = aws_s3_bucket_versioning.journal
  id = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
}

import {
  to = aws_s3_bucket_lifecycle_configuration.backup
  id = "fuminosuke-beta-backupbucket-qfvbkownbpey"
}

