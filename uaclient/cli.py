#!/usr/bin/env python

"""\
Client to manage Ubuntu Advantage support entitlements on a machine.

Available entitlements:
 - Extended Support and Maintenance (https://ubuntu.com/esm)
 - Federal Information Processing Standards
 - Federal Information Processing Standards Updates
 - Canonical Livepatch (https://www.ubuntu.com/server/livepatch)

"""

import argparse
from datetime import datetime
import logging
import os
import sys
import textwrap

from uaclient import config
from uaclient import contract
from uaclient import entitlements
from uaclient import sso
from uaclient import status as ua_status
from uaclient import util

NAME = 'ubuntu-advantage-client'

USAGE_TMPL = '{name} {command} [flags]'
EPILOG_TMPL = (
    'Use {name} {command} --help for more information about a command.')

STATUS_HEADER_TMPL = """\
Account: {account}
Subscription: {subscription}
Valid until: {contract_expiry}
Technical support: {support_level}
"""

DEFAULT_LOG_FORMAT = '%(asctime)s - %(filename)s:(%(lineno)d) [%(levelname)s]: %(message)s'


def attach_parser(parser=None):
    """Build or extend an arg parser for attach subcommand."""
    usage = USAGE_TMPL.format(name=NAME, command='attach [token]')
    if not parser:
        parser = argparse.ArgumentParser(
            prog='attach',
            description=('Attach this machine to an existing Ubuntu Advantage'
                         ' support subscription'),
            usage=usage)
    else:
        parser.usage = usage
        parser.prog = 'attach'
    parser._optionals.title = 'Flags'
    parser.add_argument(
        'token', nargs='?',
        help='Optional token obtained from Ubuntu Advantage dashboard')
    parser.add_argument(
        '--email', action='store',
        help='Optional email address for Ubuntu SSO login')
    parser.add_argument(
        '--password', action='store',
        help='Optional password for Ubuntu SSO login')
    parser.add_argument(
        '--otp', action='store',
        help=('Optional one-time password for login to Ubuntu Advantage'
              ' Dashboard'))
    return parser


def detach_parser(parser=None):
    """Build or extend an arg parser for detach subcommand."""
    usage = USAGE_TMPL.format(name=NAME, command='detach')
    if not parser:
        parser = argparse.ArgumentParser(
            prog='detach',
            description=(
                'Detach this machine from an existing Ubuntu Advantage'
                ' support subscription'),
            usage=usage)
    else:
        parser.usage = usage
        parser.prog = 'detach'
    parser._optionals.title = 'Flags'
    return parser


def enable_parser(parser=None):
    """Build or extend an arg parser for enable subcommand."""
    usage = USAGE_TMPL.format(name=NAME, command='enable') + ' <entitlement>'
    if not parser:
        parser = argparse.ArgumentParser(
            prog='enable',
            description='Enable a support entitlement on this machine',
            usage=usage)
    else:
        parser.usage = usage
        parser.prog = 'enable'
    parser._positionals.title = 'Entitlements'
    parser._optionals.title = 'Flags'
    entitlement_names = list(
        cls.name for cls in entitlements.ENTITLEMENT_CLASSES)
    parser.add_argument(
        'name', action='store', choices=entitlement_names,
        help='The name of the support entitlement to enable')
    return parser


def disable_parser(parser=None):
    """Build or extend an arg parser for disable subcommand."""
    usage = USAGE_TMPL.format(name=NAME, command='disable') + ' <entitlement>'
    if not parser:
        parser = argparse.ArgumentParser(
            prog='disable',
            description='Disable a support entitlement on this machine',
            usage=usage)
    else:
        parser.usage = usage
        parser.prog = 'disable'
    parser._positionals.title = 'Entitlements'
    parser._optionals.title = 'Flags'
    entitlement_names = list(
        cls.name for cls in entitlements.ENTITLEMENT_CLASSES)
    parser.add_argument(
        'name', action='store', choices=entitlement_names,
        help='The name of the support entitlement to disable')
    return parser


def status_parser(parser=None):
    """Build or extend an arg parser for status subcommand."""
    usage = USAGE_TMPL.format(name=NAME, command='status')
    if not parser:
        parser = argparse.ArgumentParser(
            prog='status',
            description=('Print status information for Ubuntu Advantage'
                         ' support subscription'),
            usage=usage)
    else:
        parser.usage = usage
        parser.prog = 'status'
    parser._optionals.title = 'Flags'
    parser.add_argument(
        '--update-motd', action='store_true', dest='update_motd',
        help='Write motd cache files with current ubuntu-advantage status')
    return parser


def action_disable(args, cfg):
    """Perform the disable action on a named entitlement.

    @return: 0 on success, 1 otherwise
    """
    ent_cls = entitlements.ENTITLEMENT_CLASS_BY_NAME[args.name]
    entitlement = ent_cls(cfg)
    return 0 if entitlement.disable() else 1


def action_enable(args, cfg):
    """Perform the enable action on a named entitlement.

    @return: 0 on success, 1 otherwise
    """
    ent_cls = entitlements.ENTITLEMENT_CLASS_BY_NAME[args.name]
    entitlement = ent_cls(cfg)
    return 0 if entitlement.enable() else 1


def action_detach(args, cfg):
    """Perform the detach action for this machine.

    @return: 0 on success, 1 otherwise
    """
    if not cfg.is_attached:
        print(textwrap.dedent("""\
            This machine is not attached to a UA Subscription, sign up here:
                  https://ubuntu.com/advantage
        """))
        return 1
    if os.getuid() != 0:
        print(ua_status.MESSAGE_NONROOT_USER)
        return 1
    for ent_cls in entitlements.ENTITLEMENT_CLASSES:
        ent = ent_cls(cfg)
        if ent.can_disable(silent=True):
            ent.disable(silent=True)
    cfg.delete_cache('machine-token')
    cfg.delete_cache('oauth')
    print('This machine is now detached')
    return 0


def action_attach(args, cfg):
    if cfg.is_attached:
        print("This machine is already attached to '%s'." %
              cfg.accounts[0]['name'])
        return 0
    if os.getuid() != 0:
        print(ua_status.MESSAGE_NONROOT_USER)
        return 1
    contract_client = contract.UAContractClient(cfg)
    if not args.token:
        try:
            root_macaroon = contract_client.request_root_macaroon()
            caveat_id = sso.extract_macaroon_caveat_id(
                root_macaroon['macaroon'])
            user_token = sso.prompt_request_macaroon(cfg, caveat_id)
        except (util.UrlError) as e:
            logging.error("Could not reach url '%s' to authenticate.", e.url)
            return 1
        sso_token = sso.bind_discharge_macarooon_to_root_macaroon(
            user_token['discharge_macaroon'], root_macaroon['macaroon'])
    else:
        sso_token = args.token
    if not sso_token:
        print('Could not attach machine. Unable to obtain authenticated user'
              ' token')
        return 1
    cfg.write_cache('bound-macaroon', sso_token)
    try:
        contract_client.request_accounts(sso_token)
        contracts = contract_client.request_account_contracts(sso_token)
        contract_id = contracts['contracts'][0]['contractInfo']['id']
        contract_token = contract_client.request_add_contract_token(
            sso_token, contract_id)
        token_response = contract_client.request_contract_machine_attach(
            contract_token=contract_token['contractToken'])
    except (sso.SSOAuthError, util.UrlError) as e:
        logging.error(str(e))
        return 1
    contractInfo = token_response['machineTokenInfo']['contractInfo']
    for entitlement_name in contractInfo['resourceEntitlements'].keys():
        # Obtain each entitlement's accessContext for this machine
        contract_client.request_resource_machine_access(
            cfg.machine_token, entitlement_name)
    print("This machine is now attached to '%s'.\n" %
          token_response['machineTokenInfo']['contractInfo']['name'])
    action_status(args=None, cfg=cfg)
    return 0


def get_parser():
    parser = argparse.ArgumentParser(
        prog=NAME, formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
        usage=USAGE_TMPL.format(name=NAME, command='[command]'),
        epilog=EPILOG_TMPL.format(name=NAME, command='[command]'))
    parser.add_argument(
        '--debug', action='store_true', help='Show all debug messages')
    parser._optionals.title = 'Flags'
    subparsers = parser.add_subparsers(
        title='Available Commands', dest='command', metavar='')
    subparsers.required = True
    parser_status = subparsers.add_parser(
        'status', help='current status of all ubuntu advantage entitlements')
    parser_status.set_defaults(action=action_status)
    status_parser(parser_status)
    parser_attach = subparsers.add_parser(
        'attach',
        help='attach this machine to an ubuntu advantage subscription')
    attach_parser(parser_attach)
    parser_attach.set_defaults(action=action_attach)
    parser_detach = subparsers.add_parser(
        'detach',
        help='remove this machine from an ubuntu advantage subscription')
    detach_parser(parser_detach)
    parser_detach.set_defaults(action=action_detach)
    parser_enable = subparsers.add_parser(
        'enable',
        help='enable a specific support entitlement on this machine')
    enable_parser(parser_enable)
    parser_enable.set_defaults(action=action_enable)
    parser_disable = subparsers.add_parser(
        'disable',
        help='disable a specific support entitlement on this machine')
    disable_parser(parser_disable)
    parser_disable.set_defaults(action=action_disable)
    parser_version = subparsers.add_parser(
        'version', help='Show version of ua-client')
    parser_version.set_defaults(action=print_version)
    return parser


def action_status(args, cfg):
    update_motd = bool(args and args.update_motd)
    if not cfg:
        cfg = config.UAConfig()
    if update_motd:
        esm_status = ua_status.get_motd_summary(cfg, esm_only=True)
        util.write_file(config.MOTD_UPDATES_AVAILABLE_CACHE_FILE, esm_status)
        ua_motd_status = ua_status.get_motd_summary(cfg)
        util.write_file(config.MOTD_CACHE_FILE, ua_motd_status)
        print(esm_status)
        print(ua_motd_status)
    if not cfg.is_attached:
        print(ua_status.MESSAGE_UNATTACHED)
        return
    contractInfo = cfg.machine_token['machineTokenInfo']['contractInfo']
    expiry = datetime.strptime(
        contractInfo['effectiveTo'], '%Y-%m-%dT%H:%M:%S.%fZ')

    status_content = []
    # TODO(parse-from-TBD contract-api-resourceEntitlement-response)
    status_level = ua_status.STATUS_COLOR.get(
            ua_status.STANDARD, ua_status.STANDARD)
    account = cfg.accounts[0]
    status_content.append(STATUS_HEADER_TMPL.format(
        account=account['name'],
        subscription=contractInfo['name'],
        contract_expiry=expiry.date(),
        support_level=status_level))

    for ent_cls in entitlements.ENTITLEMENT_CLASSES:
        ent = ent_cls(cfg)
        status_content.append(ua_status.format_entitlement_status(ent))
    status_content.append('\nEnable entitlements with `ua enable <service>`\n')
    print('\n'.join(status_content))


def print_version(_args=None, _cfg=None):
    print(config.get_version())


def setup_logging(level=logging.ERROR, log_file=None):
    """Setup console logging and debug logging to log_file"""
    if log_file is None:
        log_file = config.CONFIG_DEFAULTS['log_file']
    fmt = '[%(levelname)s]: %(message)s'
    console_formatter = logging.Formatter(fmt)
    log_formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    root = logging.getLogger()
    # Setup debug file logging
    filehandler = logging.FileHandler(log_file)
    filehandler.setLevel(logging.DEBUG)
    filehandler.setFormatter(log_formatter)
    root.addHandler(filehandler)
    # Setup console logging
    stderr_found = False
    for handler in root.handlers:
        if hasattr(handler, 'stream') and hasattr(handler.stream, 'name'):
            if handler.stream.name == '<stderr>':
                handler.setLevel(level)
                handler.setFormatter(console_formatter)
                stderr_found = True
                break
    if not stderr_found:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(console_formatter)
        console.setLevel(level)
        root.addHandler(console)
    root.setLevel(level)


def main(sys_argv=None):
    if not sys_argv:
        sys_argv = sys.argv
    parser = get_parser()
    args = parser.parse_args(args=sys_argv[1:])
    cfg = config.UAConfig()
    log_level = logging.DEBUG if args.debug else cfg.log_level
    log_file = logging.DEBUG if args.debug else cfg.log_file
    setup_logging(log_level, log_file)
    return args.action(args, cfg)


if __name__ == '__main__':
    sys.exit(main())