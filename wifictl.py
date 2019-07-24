#!/usr/bin/python3
"""Helpers to control the Wifi interface(s) of a Raspberry Pi"""
import re
import subprocess
import time

import wifi
import wifi.exceptions

run = subprocess.check_output


def replace_in_file(filepath, source, target, regex=False):
    # TODO: not yet failsafe, i.e. when source differs slightly, target is not even inserted...
    if not regex:
        source = re.escape(source)
    with open(filepath, "r") as file:
        content = file.read()
    with open(filepath, "w") as file:
        file.write(re.sub(source, target, content, flags=re.DOTALL))


DHCPCD_CONF_ENTRY_TEMPLATE = """
interface {iface}
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
"""


class WifiController(object):
    """Provides a high-level interface for the configuration of wifi networks on a Raspberry Pi.
    Two modes: AP (=Access point) mode and CLI (client) mode,
    requires rebooting after changing the mode
    """
    WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
    DHCPCD_CONF = "/etc/dhcpcd.conf"
    HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"

    AP_MODE = "AP"
    CLI_MODE = "CLI"

    def __init__(self, interface="wlan0"):
        self.interface = interface
        self.dhcpcd_conf_entry = DHCPCD_CONF_ENTRY_TEMPLATE.format(iface=self.interface).strip()

    def list_available_wifis(self):
        cells = []
        for i in range(3):
            try:
                cells = wifi.Cell.all(self.interface)
                break
            except wifi.exceptions.InterfaceError:
                run(("ifconfig", self.interface, "up"))
                time.sleep(2)
        unique_cells = list({cell.ssid: cell for cell in cells}.values())
        unique_cells.sort(key=lambda cell: cell.quality, reverse=True)
        return unique_cells

    def set_cli_mode(self, ssid=None, password=None):
        """Goes into CLI_MODE, i.e. connects to an available wifi network using the given ssid and password."""
        if not password:
            print("[WARNING] Unencrypted networks are not implemented yet.")  # TODO
            return False

        run(("dhcpcd", "--release", self.interface))
        run(("service", "dhcpcd", "stop"))

        if ssid:
            # Hashes the password (-> PSK) and generates an entry for wpa_supplicant.conf
            entry = run(("/usr/bin/wpa_passphrase", ssid, password)).decode()
            entry = re.sub(r'\t*#psk="[^\n"]+"\n', "", entry)  # removes plain text password
            self.add_entry_to_config(entry, self.WPA_SUPPLICANT_CONF)

        self.remove_entry_from_config(self.DHCPCD_CONF)

        # Stop & disable hostapd and dnsmasq services
        run(("systemctl", "stop", "hostapd.service", "dnsmasq.service"))
        run(("systemctl", "disable", "hostapd.service", "dnsmasq.service"))
        run(("service", "dhcpcd", "start"))
        time.sleep(3)

        run(("wpa_cli", "-i", self.interface, "reconfigure"))

        # TODO
        # while not_connected():
        #     time.sleep(1)
        return True

    def set_ap_mode(self):
        """Goes into AP_MODE, i.e. provides a wifi by itself."""
        run(("dhcpcd", "--release", self.interface))
        run(("service", "dhcpcd", "stop"))

        self.add_entry_to_config(self.dhcpcd_conf_entry, self.DHCPCD_CONF)

        # Enable hostapd and dnsmasq services
        run(("systemctl", "enable", "hostapd.service", "dnsmasq.service"))
        run(("systemctl", "start", "hostapd.service", "dnsmasq.service"))
        run(("service", "dhcpcd", "start"))

    def get_mode(self):
        if subprocess.call(["systemctl", "-q", "is-active", "hostapd"]) == 0:
            return self.AP_MODE
        else:
            return self.CLI_MODE

    def set_ap_credentials(self, wifi_name, wifi_password):
        replace_in_file(self.HOSTAPD_CONF, r"\bssid=[^\n]+\b", "ssid=%s" % wifi_name, regex=True)
        replace_in_file(self.HOSTAPD_CONF, r"\bwpa_passphrase=[^\n]+\b", "wpa_passphrase=%s" % wifi_password,
                        regex=True)
        if self.get_mode() == WifiController.AP_MODE:
            run(("systemctl", "restart", "hostapd.service"))

    @staticmethod
    def add_entry_to_config(entry, filepath, identifier="rpiwifi"):
        WifiController.remove_entry_from_config(filepath, identifier)
        with open(filepath, "a") as f:
            f.write("#<begin_{id}_entry>\n".format(id=identifier)
                    + entry + "\n"
                    + "#<end_{id}_entry>\n".format(id=identifier))

    @staticmethod
    def remove_entry_from_config(filepath, identifier="rpiwifi"):
        replace_in_file(filepath, r"#<begin_{id}_entry>.+#<end_{id}_entry>".format(id=identifier), "", regex=True)


if __name__ == '__main__':
    import argparse, sys, os

    if os.geteuid() != 0:
        exit("You need to have root privileges to run this script.\nPlease try again, this time using 'sudo'. Exiting.")

    wifi_controller = WifiController()

    parser = argparse.ArgumentParser()
    # parser.add_argument("command", type=str)
    subparsers = parser.add_subparsers(title="command")

    list_parser = subparsers.add_parser("list_wifis")
    list_parser.set_defaults(func=wifi_controller.list_available_wifis)

    set_cli_mode_parser = subparsers.add_parser("set_cli_mode")
    set_cli_mode_parser.add_argument("ssid", type=str, nargs="?")
    set_cli_mode_parser.add_argument("password", type=str, nargs="?")
    set_cli_mode_parser.set_defaults(func=wifi_controller.set_cli_mode)

    set_ap_mode_parser = subparsers.add_parser("set_ap_mode")
    set_ap_mode_parser.set_defaults(func=wifi_controller.set_ap_mode)

    get_mode_parser = subparsers.add_parser("get_mode")
    get_mode_parser.set_defaults(func=wifi_controller.get_mode)

    set_ap_credentials_parser = subparsers.add_parser("set_ap_credentials")
    set_ap_credentials_parser.add_argument("wifi_name", type=str)
    set_ap_credentials_parser.add_argument("wifi_password", type=str)
    set_ap_credentials_parser.set_defaults(func=wifi_controller.set_ap_credentials)

    args = parser.parse_args(sys.argv[1:])
    kwargs = vars(args).copy()
    del kwargs["func"]
    print(args.func(**kwargs))
