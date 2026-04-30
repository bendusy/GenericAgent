[Unit]
Description=GenericAgent fsapp (Feishu bot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={{REPO}}
ExecStart={{PYTHON}} {{REPO}}/frontends/fsapp.py
Restart=on-failure
RestartSec=30
StandardOutput=append:{{LOG_DIR}}/fsapp.out.log
StandardError=append:{{LOG_DIR}}/fsapp.err.log
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=LARK_CLI={{LARK_CLI_PATH}}

[Install]
WantedBy=default.target
