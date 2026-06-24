# fork-only：hub.pyw 的私有 service 扩展（不入上游）。
#
# DEPRECATED（2026-06-25）：生产链路走 launchd（com.genericagent.*），不走 hub.pyw。
#   - claude-max-proxy 由 com.genericagent.claudemaxproxy 这一个 launchd agent 独管，
#     这里不再注入 infra/claude-max-proxy（避免与 launchd 抢 :5678 端口竞态）。
#   - GA_LLM_NOS / GA_CLAUDE_PROXY_URL 是死环变（fsapp.py 不消费）；LLM 链真源是
#     mykey.py mixin_config['llm_nos']，代理 URL 真源是 mykey official.apibase。
#   仅保留 agent-bbs 的按需注入，供仍用 hub.pyw 的本地调试场景。
import os
import sys

BBS_PORT = int(os.environ.get('BBS_PORT', '58800'))


def apply(services, base_dir):
    # 仅注入 BBS（proxy 由 launchd 独管，环变已废弃，见模块顶部说明）。
    bbs_py = os.path.join(base_dir, 'assets', 'agent_bbs.py')
    if os.path.isfile(bbs_py):
        services[:0] = [{
            'name': 'infra/agent-bbs',
            'cmd': [sys.executable, 'agent_bbs.py'],
            'cwd': os.path.join(base_dir, 'assets'),
        }]
