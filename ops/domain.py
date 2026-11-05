# File: ops/domain.py
# Summary: Validate the explicit deployment domain before privileged scripts construct configuration paths.

import os
import re


def require_domain():
    """Accept DNS labels only, preventing a host setting from becoming a shell expression or file path."""
    domain = os.environ.get('JITSI_DOMAIN', '').strip().rstrip('.').lower()
    labels = domain.split('.')
    if (not domain or domain == 'meet.example.com' or len(domain) > 253 or len(labels) < 2
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('Set JITSI_DOMAIN to the fully qualified domain of your existing Jitsi deployment')
    return domain
