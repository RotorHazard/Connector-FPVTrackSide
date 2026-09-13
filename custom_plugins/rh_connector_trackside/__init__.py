''' Trackside Connector '''

import logging
import json
from time import monotonic
from RHRace import RaceStatus
from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption
from RHUtils import HEAT_ID_NONE

logger = logging.getLogger(__name__)

class TracksideConnector():
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self.enabled = False
        self._trackside_race_id = None
        self._lean_heat_id = None

        self._rhapi.events.on(Evt.RACE_LAP_RECORDED, self.race_lap_recorded)
        self._rhapi.events.on(Evt.LAPS_SAVE, self.laps_save)
        self._rhapi.events.on(Evt.LAPS_CLEAR, self.laps_clear)
        self._rhapi.events.on(Evt.LAPS_RESAVE, self.laps_resave)


    def initialize(self, _args):
        logger.info('Initializing Trackside connector')

        self._rhapi.ui.socket_listen('ts_server_info', self.server_info)
        self._rhapi.ui.socket_listen('ts_server_time', self.server_time)
        self._rhapi.ui.socket_listen('ts_frequency_setup', self.frequency_setup)
        self._rhapi.ui.socket_listen('ts_color_setup', self.color_setup)
        self._rhapi.ui.socket_listen('ts_get_lean_mode', self.get_lean_mode)
        self._rhapi.ui.socket_listen('ts_set_lean_mode', self.set_lean_mode)

        self._rhapi.ui.socket_listen('ts_race_stage', self.race_stage)
        self._rhapi.ui.socket_listen('ts_race_stop', self.race_stop)
        self._rhapi.ui.socket_listen('ts_race_abort', self.race_abort)

        self._rhapi.fields.register_race_attribute(UIField('trackside_race_ID', "FPVTrackSide Race ID", UIFieldType.TEXT, private=True))
        self._rhapi.fields.register_pilot_attribute(UIField('trackside_pilot_ID', "Trackside Pilot ID", UIFieldType.TEXT, private=True))

        self._rhapi.ui.register_panel('ts_connector', "FPVTrackSide Connector", 'settings', order=0)
        self._rhapi.fields.register_option(
            UIField('_ts_lean_mode', "Lean mode (do not save races)",
                    desc="Reuse a single heat and never save races or rebuild results. "
                         "Much faster on Raspberry Pi 3/4 and large databases, and the "
                         "database stops growing. Lap timing and the ELRS OSD are "
                         "unaffected. Disables adaptive calibration, marshalling and "
                         "RotorHazard's own results pages. Requires a restart of the "
                         "race to take effect.",
                    field_type=UIFieldType.CHECKBOX),
            'ts_connector')

    def server_info(self, _arg=None):
        self.enabled = True
        info = {
            'name': self._rhapi.config.get('UI', 'timerName'),
            'logo': self._rhapi.config.get('UI', 'timerLogo'),
            'hue_primary': self._rhapi.config.get('UI', 'hue_0'),
            'sat_primary': self._rhapi.config.get('UI', 'sat_0'),
            'lum_primary': self._rhapi.config.get('UI', 'lum_0_low'),
            'contrast_primary': self._rhapi.config.get('UI', 'contrast_0_low'),
            'hue_secondary': self._rhapi.config.get('UI', 'hue_1'),
            'sat_secondary': self._rhapi.config.get('UI', 'sat_1'),
            'lum_secondmary': self._rhapi.config.get('UI', 'lum_1_low'),
            'contrast_secondmary': self._rhapi.config.get('UI', 'contrast_1_low'),
        }
        info.update(self._rhapi.server_info)
        return info

    def server_time(self, _arg=None):
        self.enabled = True
        return monotonic()

    def frequency_setup(self, arg=None):
        self.enabled = True
        frequency_set = self._rhapi.race.frequencyset
        self._rhapi.db.frequencyset_alter(frequency_set.id, frequencies=arg)

    def _lean_mode(self) -> bool:
        """True when the connector should avoid creating heats and saving races."""
        return self._rhapi.db.option('_ts_lean_mode') == '1'

    def get_lean_mode(self, _arg=None):
        """Return the configured mode as a Socket.IO acknowledgement."""
        return {'lean_mode': self._lean_mode()}

    def set_lean_mode(self, arg=None):
        """Change the option between races, retaining any pending race data."""
        if not isinstance(arg, dict) or not isinstance(arg.get('lean_mode'), bool):
            return dict(self.get_lean_mode(), error='lean_mode must be a boolean')

        lean_mode = arg['lean_mode']
        if lean_mode == self._lean_mode():
            return self.get_lean_mode()

        if self._rhapi.race.status != RaceStatus.READY:
            return dict(self.get_lean_mode(), error='Race must be ready before changing lean mode')

        self._rhapi.db.option_set('_ts_lean_mode', '1' if lean_mode else '0')
        self._rhapi.ui.broadcast_ui('settings')
        return self.get_lean_mode()

    def _pilot_map(self):
        """callsign -> pilot, and trackside_pilot_ID -> pilot, built with 2 queries.

        The default path queries pilot_attribute_value() once per (ts pilot x rh pilot),
        which is O(n*m) round trips. Building both maps up front is equivalent and costs
        one pass over the pilot list.
        """
        by_callsign = {}
        by_ts_id = {}
        for pilot in self._rhapi.db.pilots:
            by_callsign[pilot.callsign] = pilot
            ts_id = self._rhapi.db.pilot_attribute_value(pilot.id, 'trackside_pilot_ID', None)
            if ts_id:
                by_ts_id[ts_id] = pilot
        return by_callsign, by_ts_id

    def _lean_heat(self):
        """Fetch (or create once) the single reusable heat used in lean mode."""
        if self._lean_heat_id is not None:
            heat = self._rhapi.db.heat_by_id(self._lean_heat_id)
            if heat:
                return heat
            self._lean_heat_id = None

        for heat in self._rhapi.db.heats:
            if self._rhapi.db.heat_attribute_value(heat.id, 'trackside_lean_heat', None) == '1':
                self._lean_heat_id = heat.id
                return heat

        heat = self._rhapi.db.heat_add(name="FPVTrackSide")
        self._rhapi.db.heat_alter(heat.id, attributes={'trackside_lean_heat': '1'})
        self._lean_heat_id = heat.id
        logger.info("Lean mode: created reusable heat %s", heat.id)
        return heat

    def race_stage(self, arg=None):
        if not arg:
            return None

        self.enabled = True

        if self._rhapi.race.status != RaceStatus.READY:
            self._rhapi.race.stop() #doSave executes asynchronously, but we need it done now
            if self._lean_mode():
                # Lean mode never persists a race, so there is nothing to flush and no
                # results rebuild to trigger.
                self._rhapi.race.clear()
            else:
                self._rhapi.race.save()

        if arg.get('p'):
            ts_pilot_callsigns = arg.get('p')
            ts_pilot_ids = arg.get('p_id')
            race_number = arg.get('race_number')
            round_number = arg.get('round_number')
            bracket = arg.get('bracket')

            if self._lean_mode():
                self._stage_lean(ts_pilot_callsigns, ts_pilot_ids)
            else:
                self._stage_full(ts_pilot_callsigns, ts_pilot_ids,
                                 race_number, round_number, bracket)

        start_race_args = {
            'secondary_format': True,
            'ignore_secondary_heat': True,
        }

        if arg.get('start_time_s'):
            start_race_args['start_time_s'] = arg['start_time_s']

        self._trackside_race_id = arg.get('race_id')

        self._rhapi.race.stage(start_race_args)

    def _stage_lean(self, ts_pilot_callsigns, ts_pilot_ids):
        """Reuse one heat; update its slots in place. No heat/race rows are created."""
        heat = self._lean_heat()
        by_callsign, by_ts_id = self._pilot_map()

        slots = self._rhapi.db.slots_by_heat(heat.id)
        slot_list = []
        added_pilot = False

        for idx, callsign in enumerate(ts_pilot_callsigns):
            ts_id = ts_pilot_ids[idx] if ts_pilot_ids and idx < len(ts_pilot_ids) else None

            pilot = by_ts_id.get(ts_id) if ts_id else None
            if pilot is None:
                pilot = by_callsign.get(callsign)
                if pilot is not None and ts_id:
                    self._rhapi.db.pilot_alter(pilot.id, attributes={'trackside_pilot_ID': ts_id})
            if pilot is None:
                pilot = self._rhapi.db.pilot_add(name=callsign, callsign=callsign)
                self._rhapi.db.pilot_alter(pilot.id, attributes={'trackside_pilot_ID': ts_id})
                by_callsign[callsign] = pilot
                added_pilot = True

            for slot in slots:
                if slot.node_index == idx:
                    slot_list.append({'slot_id': slot.id, 'pilot': pilot.id})
                    break

        if slot_list:
            self._rhapi.db.slots_alter_fast(slot_list)

        # Point the race at the reusable heat. set_heat() refreshes node_pilots, which is
        # what the ELRS OSD reads; it is a no-op for the DB when the heat is unchanged.
        self._rhapi.race.heat = heat.id

        if added_pilot:
            self._rhapi.ui.broadcast_pilots()
        self._rhapi.ui.broadcast_current_heat()

    def _stage_full(self, ts_pilot_callsigns, ts_pilot_ids, race_number, round_number, bracket):
        """Original behaviour: a new heat per race, races saved, results rebuilt."""
        heat = self._rhapi.db.heat_add()
        if race_number and race_number > 0:
            if bracket:
                heat_name = "{} {}: {} {} · {} {} · {} {}".format(
                    self._rhapi.__("Heat"), heat.id,
                    self._rhapi.__("Bracket"), bracket,
                    self._rhapi.__("Round"), round_number,
                    self._rhapi.__("Race"), race_number)
            else:
                heat_name = "{} {}: {} {} · {} {}".format(
                    self._rhapi.__("Heat"), heat.id,
                    self._rhapi.__("Round"), round_number,
                    self._rhapi.__("Race"), race_number)
        else:
            heat_name = "TrackSide {} {}".format(self._rhapi.__("Heat"), heat.id)

        self._rhapi.db.heat_alter(heat.id, name=heat_name)
        slots = self._rhapi.db.slots_by_heat(heat.id)
        slot_list = []
        rh_pilots = self._rhapi.db.pilots
        added_pilot = False
        for idx, ts_pilot_callsign in enumerate(ts_pilot_callsigns):
            ts_id = ts_pilot_ids[idx] if ts_pilot_ids and idx < len(ts_pilot_ids) else None
            for rh_pilot in rh_pilots:
                rh_pilot_ts_id = self._rhapi.db.pilot_attribute_value(rh_pilot.id, 'trackside_pilot_ID', None)
                if ts_id and rh_pilot_ts_id == ts_id:
                    pilot = rh_pilot
                    break
                else:
                    if rh_pilot.callsign == ts_pilot_callsign:
                        pilot = rh_pilot
                        self._rhapi.db.pilot_alter(pilot.id, attributes={
                            'trackside_pilot_ID': ts_id
                        })
                        break
            else:
                new_pilot = self._rhapi.db.pilot_add(name=ts_pilot_callsign, callsign=ts_pilot_callsign)
                self._rhapi.db.pilot_alter(new_pilot.id, attributes={
                    'trackside_pilot_ID': ts_id
                })
                pilot = new_pilot
                added_pilot = True

            for slot in slots:
                if slot.node_index == idx:
                    slot_list.append({
                        'slot_id': slot.id,
                        'pilot': pilot.id
                    })
                    break

        self._rhapi.db.slots_alter_fast(slot_list)
        self._rhapi.race.heat = heat.id

        if added_pilot:
            self._rhapi.ui.broadcast_pilots()
        self._rhapi.ui.broadcast_heats()
        self._rhapi.ui.broadcast_current_heat()

    def race_lap_recorded(self, args):
        if self.enabled:
            payload = {
                'seat': args['node_index'],
                'frequency': args['frequency'],
                'peak_rssi': args['peak_rssi'],
                'lap_time': args['lap'].lap_time_stamp / 1000,
            }
            self._rhapi.ui.socket_broadcast('ts_lap_data', payload)

    def race_stop(self, arg=None):
        self._rhapi.race.stop()

    def race_abort(self, arg=None):
        self._rhapi.race.clear()
        current_heat = self._rhapi.race.heat
        all_heats = self._rhapi.db.heats
        self._rhapi.race.heat = HEAT_ID_NONE
        self._rhapi.db.heat_delete(current_heat)
        self._rhapi.ui.broadcast_heats()

    def laps_save(self, args):
        race_id = args.get('race_id')
        if race_id and self._trackside_race_id:
            self._rhapi.db.race_alter(race_id, attributes = {
                'trackside_race_ID': self._trackside_race_id
            })

    def laps_clear(self, args):
        self._trackside_race_id = None

    def laps_resave(self, args):
        if args and args.get('race_id'):
            race_id = args.get('race_id')
            for run in self._rhapi.db.pilotruns_by_race(race_id):
                if run.pilot_id == args.get('pilot_id'):
                    laps_raw = self._rhapi.db.laps_by_pilotrun(run.id)
                    laps = []
                    for lap in laps_raw:
                        laps.append({
                            'deleted': lap.deleted,
                            'lap_time': lap.lap_time,
                            'lap_time_formatted': lap.lap_time_formatted,
                            'lap_time_stamp': lap.lap_time_stamp,
                        })    
                    break
            else:
                return False

            ts_race_id = self._rhapi.db.race_attribute_value(race_id, 'trackside_race_ID')

            pilot_id = args.get('pilot_id')
            callsign = self._rhapi.db.pilot_by_id(pilot_id).callsign
            ts_pilot_id = self._rhapi.db.pilot_attribute_value(pilot_id, 'trackside_pilot_ID', None)

            payload = {
                'race_id': ts_race_id,
                'callsign': callsign,
                'ts_pilot_id': ts_pilot_id,
                'laps': laps
            }
            self._rhapi.ui.socket_broadcast('ts_race_marshal', payload)

    def color_setup(self, arg):
        if arg.get('channel_color'):
            self._rhapi.config.set('LED', 'ledColorMode', 0)  # TS supports only "seat" mode
            self._rhapi.config.set('LED', 'seatColors', arg.get('channel_color'))
            self._rhapi.race.update_colors()
            self._rhapi.ui.broadcast_pilots()
            self._rhapi.ui.broadcast_heats()

def initialize(rhapi):
    connector = TracksideConnector(rhapi)
    rhapi.events.on(Evt.STARTUP, connector.initialize)

