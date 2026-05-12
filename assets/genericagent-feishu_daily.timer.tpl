[Unit]
Description=Run feishu_hub daily report at 21:00 local

[Timer]
OnCalendar=*-*-* 21:00:00
Persistent=true

[Install]
WantedBy=timers.target
