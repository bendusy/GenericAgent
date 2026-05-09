[Unit]
Description=GenericAgent wechatapp (WeChat bot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={{REPO}}
ExecStart={{PYTHON}} {{REPO}}/frontends/wechatapp.py
Restart=on-failure
RestartSec=30
StandardOutput=append:{{LOG_DIR}}/wechatapp.out.log
StandardError=append:{{LOG_DIR}}/wechatapp.err.log
Environment=PATH={{LARK_BIN_DIR}}:/usr/local/bin:/usr/bin:/bin
Environment=LARK_CLI={{LARK_CLI_PATH}}

[Install]
WantedBy=default.target