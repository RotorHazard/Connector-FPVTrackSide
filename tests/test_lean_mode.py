"""Connector protocol tests using in-memory mocks; no application database."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


RaceStatus = SimpleNamespace(READY=0, RACING=1, DONE=2, STAGING=3)
plugin_path = Path(__file__).resolve().parents[1] / 'custom_plugins/rh_connector_trackside/__init__.py'
spec = importlib.util.spec_from_file_location('trackside_under_test', plugin_path)
plugin = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {
    'RHRace': SimpleNamespace(RaceStatus=RaceStatus),
    'eventmanager': SimpleNamespace(Evt=MagicMock()),
    'RHUI': SimpleNamespace(UIField=MagicMock(), UIFieldType=MagicMock(), UIFieldSelectOption=MagicMock()),
    'RHUtils': SimpleNamespace(HEAT_ID_NONE=0),
}):
    spec.loader.exec_module(plugin)


class LeanModeTests(unittest.TestCase):
    def setUp(self):
        self.options = {}
        self.rhapi = MagicMock()
        self.rhapi.db.option.side_effect = self.options.get
        self.rhapi.db.option_set.side_effect = self.options.__setitem__
        self.rhapi.race.status = RaceStatus.READY
        self.connector = plugin.TracksideConnector(self.rhapi)

    def test_registers_commands_and_calibration_warning(self):
        plugin.UIField.reset_mock()
        self.connector.initialize(None)
        self.rhapi.ui.socket_listen.assert_any_call('ts_get_lean_mode', self.connector.get_lean_mode)
        self.rhapi.ui.socket_listen.assert_any_call('ts_set_lean_mode', self.connector.set_lean_mode)
        option = next(call for call in plugin.UIField.call_args_list if call.args[0] == '_ts_lean_mode')
        self.assertIn('Disables adaptive calibration', option.kwargs['desc'])

    def test_get_reads_current_ui_option_and_defaults_off(self):
        self.assertEqual(self.connector.get_lean_mode(), {'lean_mode': False})
        self.options['_ts_lean_mode'] = '1'
        self.assertEqual(self.connector.get_lean_mode({}), {'lean_mode': True})
        self.options['_ts_lean_mode'] = '0'
        self.assertEqual(self.connector.get_lean_mode(), {'lean_mode': False})
        self.rhapi.db.option_set.assert_not_called()

    def test_set_persists_both_values_and_refreshes_settings(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.assertEqual(self.connector.set_lean_mode({'lean_mode': enabled}), {'lean_mode': enabled})
                self.assertEqual(self.options['_ts_lean_mode'], '1' if enabled else '0')
                self.assertEqual(self.connector._lean_mode(), enabled)
                self.rhapi.ui.broadcast_ui.assert_called_with('settings')
        self.assertEqual(self.rhapi.ui.broadcast_ui.call_count, 2)

    def test_invalid_payload_does_not_change_option(self):
        self.options['_ts_lean_mode'] = '1'
        for payload in (None, {}, True, False, [], 'false', {'lean_mode': None},
                        {'lean_mode': 0}, {'lean_mode': 1}, {'lean_mode': 'false'},
                        {'lean_mode': 'true'}, {'lean_mode': []}):
            with self.subTest(payload=payload):
                result = self.connector.set_lean_mode(payload)
                self.assertTrue(result['lean_mode'])
                self.assertIn('error', result)
        self.rhapi.db.option_set.assert_not_called()
        self.rhapi.ui.broadcast_ui.assert_not_called()

    def test_pending_race_rejects_mode_changes_in_both_directions(self):
        for status in (RaceStatus.STAGING, RaceStatus.RACING, RaceStatus.DONE):
            for enabled in (True, False):
                with self.subTest(status=status, enabled=enabled):
                    self.rhapi.race.status = status
                    self.options['_ts_lean_mode'] = '0' if enabled else '1'
                    result = self.connector.set_lean_mode({'lean_mode': enabled})
                    self.assertEqual(result['lean_mode'], not enabled)
                    self.assertIn('error', result)
        self.rhapi.db.option_set.assert_not_called()
        self.rhapi.ui.broadcast_ui.assert_not_called()
        self.assertEqual(self.rhapi.race.method_calls, [])

    def test_repeated_set_is_harmless_in_every_race_state(self):
        for status in (RaceStatus.READY, RaceStatus.STAGING, RaceStatus.RACING, RaceStatus.DONE):
            for enabled in (True, False):
                with self.subTest(status=status, enabled=enabled):
                    self.rhapi.race.status = status
                    self.options['_ts_lean_mode'] = '1' if enabled else '0'
                    self.assertEqual(self.connector.set_lean_mode({'lean_mode': enabled}), {'lean_mode': enabled})
        self.rhapi.db.option_set.assert_not_called()
        self.rhapi.ui.broadcast_ui.assert_not_called()


if __name__ == '__main__':
    unittest.main()
