[Unit]
Description=GenericAgent · feishu_hub daily report (21:00)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={{REPO}}
Environment=PATH={{LARK_BIN_DIR}}:/usr/local/bin:/usr/bin:/bin
Environment=LARK_CLI={{LARK_CLI_PATH}}
Environment=PYTHONPATH={{REPO}}
ExecStart={{PYTHON}} -m feishu_hub.daily_report run
StandardOutput=append:{{LOG_DIR}}/feishu_daily.out.log
StandardError=append:{{LOG_DIR}}/feishu_daily.err.log

[Install]
WantedBy=default.target
