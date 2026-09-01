import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PcsShareDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dependencies = (ROOT / "scripts" / "install-dependencies.sh").read_text(encoding="utf-8")
        cls.setup = (ROOT / "scripts" / "setup-pcs-share-discovery.sh").read_text(encoding="utf-8")
        cls.runner = (ROOT / "scripts" / "pcs-wsdd.sh").read_text(encoding="utf-8")
        cls.firewall = (ROOT / "scripts" / "pcs-wsdd-firewall.sh").read_text(encoding="utf-8")
        cls.service = (ROOT / "systemd" / "pcs-wsdd.service").read_text(encoding="utf-8")
        cls.backup_share = (ROOT / "scripts" / "setup-samba-backup-share.sh").read_text(encoding="utf-8")
        cls.base = (ROOT / "scripts" / "setup-pcs-base.sh").read_text(encoding="utf-8")
        cls.control_setup = (ROOT / "scripts" / "setup-pcs-control-panel.sh").read_text(encoding="utf-8")
        cls.self_test = (ROOT / "scripts" / "pcs-self-test.sh").read_text(encoding="utf-8")

    def test_dependency_installer_masks_unrestricted_vendor_service(self):
        self.assertIn("wsdd2", self.dependencies)
        mask = self.dependencies.index("systemctl mask wsdd2.service")
        install = self.dependencies.index("apt-get install -y")
        self.assertLess(mask, install)

    def test_discovery_runner_uses_one_responder(self):
        self.assertIn('HOST_ALIAS="pcs-file-share"', self.runner)
        self.assertIn('NETBIOS_NAME="PCS-FILE-SHARE"', self.runner)
        self.assertNotIn("INTERFACES=", self.runner)
        self.assertNotIn('-i "${interface}"', self.runner)
        self.assertIn('exec "${WSDD2}"', self.runner)
        self.assertIn('-N "${NETBIOS_NAME}"', self.runner)
        self.assertNotIn(' -w ', self.runner)

    def test_firewall_exposes_wsdd_only_on_lan_interfaces(self):
        self.assertIn('{ "lo", "eth0", "wlan0" } udp dport 3702 accept', self.firewall)
        self.assertIn('{ "lo", "eth0", "wlan0" } tcp dport 3702 accept', self.firewall)
        self.assertIn('udp dport 3702 drop', self.firewall)
        self.assertIn('tcp dport 3702 drop', self.firewall)
        self.assertIn('udp sport 3702 drop', self.firewall)
        self.assertIn('{ "lo", "eth0", "wlan0" } udp dport 5355 accept', self.firewall)
        self.assertIn('{ "lo", "eth0", "wlan0" } tcp dport 5355 accept', self.firewall)
        self.assertIn('udp dport 5355 drop', self.firewall)
        self.assertIn('tcp dport 5355 drop', self.firewall)
        self.assertIn('udp sport 5355 drop', self.firewall)
        self.assertIn('check)', self.firewall)

    def test_managed_service_is_persistent_and_hardened(self):
        self.assertIn("ExecStart=/usr/local/sbin/pcs-wsdd", self.service)
        self.assertIn("ExecStartPre=/usr/local/sbin/pcs-wsdd-firewall apply", self.service)
        self.assertIn("ExecStopPost=/usr/local/sbin/pcs-wsdd-firewall remove", self.service)
        self.assertIn("Restart=on-failure", self.service)
        self.assertIn("NoNewPrivileges=yes", self.service)
        self.assertIn("ProtectSystem=strict", self.service)
        self.assertIn("WantedBy=multi-user.target", self.service)

    def test_setup_configures_samba_avahi_and_guarded_wsdd(self):
        self.assertIn("netbios name = PCS-FILE-SHARE", self.setup)
        self.assertIn("server string = Portable Comm Server", self.setup)
        self.assertIn('<name replace-wildcards="no">PCS File Share</name>', self.setup)
        self.assertIn("<type>_smb._tcp</type>", self.setup)
        self.assertIn("systemctl mask wsdd2.service", self.setup)
        self.assertIn("systemctl enable pcs-wsdd.service", self.setup)
        self.assertIn("trap rollback ERR", self.setup)
        self.assertIn("Rollback snapshot", self.setup)
        self.assertIn("/usr/sbin/smbd", self.setup)
        self.assertIn("/usr/sbin/avahi-daemon", self.setup)
        self.assertIn("/usr/sbin/wsdd2", self.setup)
        self.assertIn("/usr/bin/testparm", self.setup)
        self.assertIn("/usr/sbin/nft", self.setup)
        self.assertIn('sudo install -o root -g root -m 0755 "${FIREWALL_SRC}" "${FIREWALL_DST}"', self.setup)
        self.assertNotIn("for command in smbd testparm avahi-daemon wsdd2", self.setup)

    def test_backup_share_uses_dedicated_admin_account(self):
        self.assertIn('BACKUP_ADMIN_USER="pcs-admin"', self.backup_share)
        self.assertIn("valid users = ${BACKUP_ADMIN_USER}", self.backup_share)
        self.assertIn("force user = ${PCS_USER}", self.backup_share)
        self.assertIn("--sync-samba-password", self.backup_share)
        self.assertNotIn("valid users = ${PCS_USER}", self.backup_share)

    def test_base_installer_repeats_complete_discovery_setup(self):
        self.assertIn('ensure_executable "scripts/setup-pcs-share-discovery.sh"', self.base)
        self.assertIn('ensure_executable "scripts/pcs-wsdd.sh"', self.base)
        self.assertIn('ensure_executable "scripts/pcs-wsdd-firewall.sh"', self.base)
        discovery = self.base.index('run_step "Configure LAN file-share discovery"')
        backup = self.base.index('run_step "Configure automatic PCS backups"')
        control = self.base.index('run_step "Install PCS Control Panel"')
        self.assertLess(backup, discovery)
        self.assertLess(discovery, control)

    def test_control_setup_creates_account_and_requires_first_sync(self):
        self.assertIn('BACKUP_ADMIN_USER="pcs-admin"', self.control_setup)
        self.assertIn("useradd --system --no-create-home --shell /usr/sbin/nologin", self.control_setup)
        self.assertIn("--sync-samba-password", self.control_setup)

    def test_self_test_covers_identity_acl_and_interface_isolation(self):
        self.assertIn('PCS_SAMBA_SERVER_ALIAS="PCS-FILE-SHARE"', self.self_test)
        self.assertIn("PCS-Backup valid users must be exactly", self.self_test)
        self.assertIn("PCS Windows file-share discovery is enabled and active", self.self_test)
        self.assertIn("The unrestricted vendor wsdd2 service is masked", self.self_test)
        self.assertIn("PCS discovery and name resolution are restricted to Ethernet and Wi-Fi", self.self_test)
        self.assertIn("PCS discovery uses one non-conflicting WSD responder", self.self_test)


if __name__ == "__main__":
    unittest.main()
