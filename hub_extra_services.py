# fork-only：hub.pyw 的私有 service 扩展（不入上游）。
# hub.pyw 调用 apply(services, base_dir)：注入 claude-max-proxy / agent-bbs
# 两个 infra service，并给 fsapp/reflect 注入走代理的环境变量。
# 与 start_fsapp_with_proxy.sh 对齐。
import os
import sys

PROXY_PORT = int(os.environ.get('PORT', '5678'))
BBS_PORT = int(os.environ.get('BBS_PORT', '58800'))
CC_MODEL = os.environ.get('CC_MODEL', 'claude-opus-4-7')
GA_LLM_NOS = os.environ.get('GA_LLM_NOS', 'opus-4-7,gpt,sonnet,opus-4-6')


def apply(services, base_dir):
    fsapp_env = {
        'GA_CLAUDE_PROXY_URL': f'http://127.0.0.1:{PROXY_PORT}',
        'GA_LLM_NOS': GA_LLM_NOS,
    }

    # 给已有的 fsapp / reflect service 补代理环境变量
    for svc in services:
        name = svc.get('name', '')
        if name.startswith('reflect/') or 'fsapp' in name:
            svc.setdefault('env', {}).update(fsapp_env)

    # 在列表头插入 infra service：proxy + BBS
    infra = []
    proxy_sh = os.path.join(base_dir, 'claude-max-proxy', 'start_proxy.sh')
    if os.path.isfile(proxy_sh):
        infra.append({
            'name': 'infra/claude-max-proxy',
            'cmd': ['bash', proxy_sh],
            'env': {'PORT': str(PROXY_PORT), 'DRY_RUN': '0', 'CC_MODEL': CC_MODEL},
        })
    bbs_py = os.path.join(base_dir, 'assets', 'agent_bbs.py')
    if os.path.isfile(bbs_py):
        infra.append({
            'name': 'infra/agent-bbs',
            'cmd': [sys.executable, 'agent_bbs.py'],
            'cwd': os.path.join(base_dir, 'assets'),
        })
    services[:0] = infra
