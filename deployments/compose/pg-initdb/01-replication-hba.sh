#!/bin/sh
# 允许容器网络的 replication 连接（pg_basebackup 做 PITR 物理备份需要）。
# postgres 官方镜像默认 pg_hba.conf 只对 localhost 开放 replication (trust)，
# 而 backup/restore 容器经 backend 网络以 superuser 连接 replication，
# 会命中 "no pg_hba.conf entry for replication connection" 被拒绝。
# 注意：initdb.d 脚本仅在数据目录首次初始化（空 PGDATA）时执行；
# 对已初始化的库需手动追加本规则并 reload。
cat >> "$PGDATA/pg_hba.conf" <<'HBA'
host replication all all scram-sha-256
HBA
