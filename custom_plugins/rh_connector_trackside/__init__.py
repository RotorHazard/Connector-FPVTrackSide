''' Trackside Connector '''

import logging
import json
import os
from time import monotonic
from RHRace import RaceStatus
from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption
from RHUtils import HEAT_ID_NONE
from Database import LapSource

logger = logging.getLogger(__name__)

class TracksideConnector():
    def __init__(self, rhapi):
        self._rhapi = rhapi
        self.enabled = False
        # FPVTrackSide's race id for the current race, used to tag it once saved (see
        # laps_save()) - only ever written by race_stage(), not cleared on Evt.LAPS_CLEAR.
        self._trackside_race_id = None
        self._race_saved = True
        # Sent to FPVTrackSide via server_info() so it can gate version-dependent features.
        self._plugin_version = self._load_plugin_version()

        self._rhapi.events.on(Evt.RACE_LAP_RECORDED, self.race_lap_recorded)
        self._rhapi.events.on(Evt.LAPS_SAVE, self.laps_save)
        self._rhapi.events.on(Evt.LAPS_RESAVE, self.laps_resave)

    def _load_plugin_version(self):
        try:
            manifest_path = os.path.join(os.path.dirname(__file__), 'manifest.json')
            with open(manifest_path, 'r') as f:
                return json.load(f).get('version')
        except Exception:
            logger.warning("Trackside connector: could not read plugin version from manifest.json")
            return None

    def initialize(self, _args):
        logger.info('Initializing Trackside connector')

        self._rhapi.ui.socket_listen('ts_server_info', self.server_info)
        self._rhapi.ui.socket_listen('ts_server_time', self.server_time)
        self._rhapi.ui.socket_listen('ts_frequency_setup', self.frequency_setup)
        self._rhapi.ui.socket_listen('ts_color_setup', self.color_setup)
        self._rhapi.ui.socket_listen('ts_event_info', self.event_info)

        self._rhapi.ui.socket_listen('ts_race_stage', self.race_stage)
        self._rhapi.ui.socket_listen('ts_race_stop', self.race_stop)
        self._rhapi.ui.socket_listen('ts_race_abort', self.race_abort)
        self._rhapi.ui.socket_listen('ts_race_marshal_update', self.race_marshal_update)
        self._rhapi.ui.socket_listen('ts_race_marshal_waveform', self.race_marshal_waveform)

        self._rhapi.fields.register_race_attribute(UIField('trackside_race_ID', "FPVTrackSide Race ID", UIFieldType.TEXT, private=True))
        self._rhapi.fields.register_pilot_attribute(UIField('trackside_pilot_ID', "Trackside Pilot ID", UIFieldType.TEXT, private=True))

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
        info['plugin_version'] = self._plugin_version
        return info

    def server_time(self, _arg=None):
        self.enabled = True
        return monotonic()

    def frequency_setup(self, arg=None):
        self.enabled = True
        frequency_set = self._rhapi.race.frequencyset
        self._rhapi.db.frequencyset_alter(frequency_set.id, frequencies=arg)

    def event_info(self, arg=None):
        '''Sets RH's own display name (shown in its header/branding) to match the FPVTrackSide
        event. Sent once whenever the event loads/changes on the FPVTrackSide side, not on
        every race - unlike race_stage, which fires every race.'''
        if not arg:
            return None

        name = arg.get('name')
        if name:
            self._rhapi.config.set('UI', 'timerName', name)

    def race_stage(self, arg=None):
        if not arg:
            return None

        self.enabled = True

        # Leftover-race safety net for a race that never got a clean race_stop (e.g.
        # FPVTrackSide disconnected mid-race) - normally _race_saved is already True here.
        if self._rhapi.race.status != RaceStatus.READY and not self._race_saved:
            self._rhapi.race.stop()
            self._rhapi.race.save()
            self._race_saved = True

        if arg.get('p'):
            ts_pilot_callsigns = arg.get('p')
            ts_pilot_ids = arg.get('p_id')
            race_number = arg.get('race_number')
            round_number = arg.get('round_number')
            bracket = arg.get('bracket')

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

        start_race_args = {
            'secondary_format': True,
            'ignore_secondary_heat': True,
        }

        if arg.get('start_time_s'):
            start_race_args['start_time_s'] = arg['start_time_s']

        # Set after race.stage() returns, not before - staging can discard the previous race
        # synchronously, and a prior version cleared this same field in response.
        stage_result = self._rhapi.race.stage(start_race_args)
        if stage_result is not False:
            self._trackside_race_id = arg.get('race_id')
            self._race_saved = False

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
        # Save immediately so the race is queryable right away, not just once the next race stages.
        self._rhapi.race.stop()
        self._rhapi.race.save()
        self._race_saved = True

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

    def _resolve_pilotrun(self, ts_race_id, ts_pilot_id):
        '''Look up the RH race_id/pilot_id/SavedPilotRace for a FPVTrackSide race_id/pilot_id
        pair, matched via the trackside_race_ID/trackside_pilot_ID attributes set by race_stage/
        laps_save. Shared by race_marshal_update and race_marshal_waveform.'''
        race_ids = self._rhapi.db.race_ids_by_attribute('trackside_race_ID', ts_race_id)
        if not race_ids:
            logger.warning("Trackside marshal: no race found for race_id %s", ts_race_id)
            return None, None, None
        race_id = race_ids[0]

        pilot_ids = self._rhapi.db.pilot_ids_by_attribute('trackside_pilot_ID', ts_pilot_id)
        if not pilot_ids:
            logger.warning("Trackside marshal: no pilot found for pilot_id %s", ts_pilot_id)
            return race_id, None, None
        pilot_id = pilot_ids[0]

        run = next((r for r in self._rhapi.db.pilotruns_by_race(race_id) if r.pilot_id == pilot_id), None)
        if not run:
            logger.warning("Trackside marshal: no pilot run found for race %s / pilot %s", race_id, pilot_id)

        return race_id, pilot_id, run

    def _alter_pilotrun(self, run, enter_at, exit_at, laps):
        '''Correct an existing saved pilot run: enter/exit calibration and/or its full lap
        list. Mirrors the same internal RHData calls the core 'resave_laps' socket handler
        (RH's own Marshal page) uses - alter_savedPilotRace/replace_savedRaceLaps, plus the
        same results-cache invalidation - so a plugin-driven correction is indistinguishable
        from one made through RH's own UI.

        RHAPI has no public method for this (only pilotrun_add, which creates a *new* run) -
        reaching into rhapi._racecontext directly here, rather than depending on a new RHAPI
        method that doesn't exist upstream, so this plugin works against a stock RH install
        with no core changes required.
        '''
        rhdata = self._rhapi._racecontext.rhdata

        if enter_at is not None or exit_at is not None:
            pilotrace_data = {'pilotrace_id': run.id}
            if enter_at is not None:
                pilotrace_data['enter_at'] = enter_at
            if exit_at is not None:
                pilotrace_data['exit_at'] = exit_at
            rhdata.alter_savedPilotRace(pilotrace_data)

        if laps is not None:
            rhdata.replace_savedRaceLaps({
                'race_id': run.race_id,
                'pilotrace_id': run.id,
                'node_index': run.node_index,
                'pilot_id': run.pilot_id,
                'laps': laps,
            })

        race = rhdata.get_savedRaceMeta(run.race_id)
        if race:
            rhdata.clear_results_heat(race.heat_id)
            rhdata.clear_results_raceClass(race.class_id)
            rhdata.clear_results_savedRaceMeta(run.race_id)

        return True

    def race_marshal_update(self, arg=None):
        '''Apply a marshal correction pushed from FPVTrackSide to a previously saved pilot run.'''
        if not arg:
            return None

        ts_race_id = arg.get('race_id')
        ts_pilot_id = arg.get('pilot_id')
        laps = arg.get('laps')

        if not ts_race_id or not ts_pilot_id or laps is None:
            logger.warning("Trackside marshal update missing race_id/pilot_id/laps")
            return None

        race_id, pilot_id, run = self._resolve_pilotrun(ts_race_id, ts_pilot_id)
        if not run:
            return None

        formatted_laps = []
        for lap in laps:
            lap_time = lap['lap_time']
            formatted_laps.append({
                'lap_time_stamp': lap['lap_time_stamp'],
                'lap_time': lap_time,
                'lap_time_formatted': self._rhapi.utils.format_time_to_str(lap_time),
                'peak_rssi': lap.get('peak_rssi', None),
                'source': lap.get('source', LapSource.API),
                'deleted': lap.get('deleted', False),
            })

        if not self._alter_pilotrun(run, arg.get('enter_at'), arg.get('exit_at'), formatted_laps):
            logger.warning("Trackside marshal update: alter failed for run %s", run.id)
            return None

        # Echo the confirmed laps back out so FPVTrackSide can reconcile.
        self.laps_resave({'race_id': race_id, 'pilot_id': pilot_id})

    def race_marshal_waveform(self, arg=None):
        '''Return the raw RSSI trace + calibration for a pilot run, on request, so FPVTrackSide's
        native marshal screen can plot it and recalculate crossings locally before committing
        anything back via race_marshal_update. Returns None if RH has no waveform on file for
        this race/pilot (e.g. history wasn't saved, or the race/pilot can't be matched).

        history_times and race_start_time are both RH's internal monotonic clock (see RHRace.py's
        start_time_monotonic) - the caller can get a race-relative offset with a plain
        subtraction, no wall-clock/epoch conversion needed.
        '''
        if not arg:
            return None

        ts_race_id = arg.get('race_id')
        ts_pilot_id = arg.get('pilot_id')

        if not ts_race_id or not ts_pilot_id:
            logger.warning("Trackside marshal waveform request missing race_id/pilot_id")
            return None

        race_id, _pilot_id, run = self._resolve_pilotrun(ts_race_id, ts_pilot_id)
        if not run or not run.history_values or not run.history_times:
            return None

        race = self._rhapi.db.race_by_id(race_id)

        return {
            'history_values': json.loads(run.history_values),
            'history_times': json.loads(run.history_times),
            'enter_at': run.enter_at,
            'exit_at': run.exit_at,
            'race_start_time': race.start_time,
        }

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

