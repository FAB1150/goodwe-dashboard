"""Specific explanations for each grid-protection parameter (the "?" buttons).

Where useful they mention the matching CEI 0-21 protection function (59.S1, 27.S2, 81>.S1, ...)
that the stage usually implements with the Italian safety country; the values themselves come
from the safety country and are what the grid operator expects.
"""

_OV = ("If the grid voltage stays ABOVE this value for longer than the matching trip time, the "
       "inverter disconnects. Overvoltage happens mostly when many PV systems on the same line export "
       "at once: disconnecting stops your inverter from pushing the voltage even higher. ")
_UV = ("If the grid voltage stays BELOW this value for longer than the matching trip time, the "
       "inverter disconnects. A very low voltage usually means a fault or that the grid has been "
       "switched off: disconnecting is part of the anti-islanding protection. ")
_OF = ("If the grid frequency stays ABOVE this value for longer than the matching trip time, the "
       "inverter disconnects. Frequency rises when there is more generation than consumption on the "
       "whole grid; it also helps detect islanding. ")
_UF = ("If the grid frequency stays BELOW this value for longer than the matching trip time, the "
       "inverter disconnects. Frequency drops when consumption exceeds generation, or when your area "
       "is cut off from the grid. ")
_TIME = ("How long the condition must last before the inverter disconnects (in seconds; the inverter "
         "counts it in 20 ms grid periods). A short time protects quickly against big deviations, a "
         "long time ignores short dips and spikes. Read-only here: SolarGo writes trip times through a "
         "firmware-dependent command, so change them from SolarGo. ")

REG_HELP: dict[int, str] = {
    # --- voltage protection ---
    0xB158: _OV + "Stage 1 is the fast, high threshold (with CEI 0-21 usually 264.5 V = 1.15 × 230 V in "
                  "0.2 s, function 59.S2).",
    0xB159: _TIME + "Applies to overvoltage stage 1.",
    0xB15A: _UV + "Stage 1 here is the very low threshold (with CEI 0-21 usually 34.5 V = 0.15 × 230 V in "
                  "0.2 s, function 27.S2): it catches a grid that has practically disappeared.",
    0xB15B: _TIME + "Applies to undervoltage stage 1.",
    0xB15C: _OV + "Stage 2 here is the slow, lower threshold (with CEI 0-21 usually 253 V = 1.10 × 230 V with "
                  "a long delay, the sustained-overvoltage function 59.S1).",
    0xB15D: _TIME + "Applies to overvoltage stage 2 (long, because it targets a sustained overvoltage).",
    0xB15E: _UV + "Stage 2 is the moderate undervoltage threshold (with CEI 0-21 usually 195.5 V = 0.85 × "
                  "230 V in about 1.5 s, function 27.S1).",
    0xB15F: _TIME + "Applies to undervoltage stage 2.",
    0xB160: ("Slow overvoltage protection: the inverter computes the voltage averaged over a sliding 10-minute "
             "window and disconnects if the average exceeds this value (CEI 0-21: 253 V = 1.10 × 230 V, "
             "function 59.S1). It limits the long-term voltage rise caused by PV export, and is the most "
             "common reason PV inverters trip on sunny days in areas with a weak grid."),
    0xB1B7: _OV + "Stage 3 is an extra threshold some grid codes use; 0 usually means disabled.",
    0xB1B8: _TIME + "Applies to overvoltage stage 3.",
    0xB1B9: _UV + "Stage 3 is an extra threshold some grid codes use; 0 usually means disabled.",
    0xB1BA: _TIME + "Applies to undervoltage stage 3.",
    # --- frequency protection ---
    0xB161: _OF + "Stage 1 here is the wide threshold (with CEI 0-21 usually 51.5 Hz in 0.1 s, function "
                  "81>.S2), always active.",
    0xB162: _TIME + "Applies to overfrequency stage 1.",
    0xB163: _UF + "Stage 1 here is the wide threshold (with CEI 0-21 usually 47.5 Hz in 0.1 s, function "
                  "81<.S2), always active.",
    0xB164: _TIME + "Applies to underfrequency stage 1.",
    0xB165: _OF + "Stage 2 here is the narrow 'restrictive' threshold (with CEI 0-21 usually 50.2 Hz in "
                  "0.1 s, function 81>.S1). In Italy the narrow thresholds are normally enabled only on "
                  "command from the grid operator; otherwise they would trip on normal grid fluctuations.",
    0xB166: _TIME + "Applies to overfrequency stage 2.",
    0xB167: _UF + "Stage 2 here is the narrow 'restrictive' threshold (with CEI 0-21 usually 49.8 Hz in "
                  "0.1 s, function 81<.S1).",
    0xB168: _TIME + "Applies to underfrequency stage 2.",
    # --- connection & reconnection ---
    0xB169: "First connection (start-up, e.g. every morning): the grid voltage must be BELOW this value for "
            "the whole observation time before the inverter connects.",
    0xB16A: "First connection: the grid voltage must be ABOVE this value for the whole observation time "
            "before the inverter connects.",
    0xB16B: "First connection: the grid frequency must be BELOW this value (CEI 0-21: 50.1 Hz) before the "
            "inverter connects. The narrow window avoids connecting while the grid is stressed.",
    0xB16C: "First connection: the grid frequency must be ABOVE this value (CEI 0-21: 49.9 Hz) before the "
            "inverter connects.",
    0xB16D: "Observation time for the first connection: voltage and frequency must stay inside the window "
            "above for this many seconds in a row before the inverter connects (CEI 0-21: 30 s).",
    0xB16E: "Reconnection after a protection trip: the voltage must be BELOW this value for the observation "
            "time before the inverter reconnects.",
    0xB16F: "Reconnection after a protection trip: the voltage must be ABOVE this value for the observation "
            "time before the inverter reconnects.",
    0xB170: "Reconnection after a protection trip: the frequency must be BELOW this value before the "
            "inverter reconnects.",
    0xB171: "Reconnection after a protection trip: the frequency must be ABOVE this value before the "
            "inverter reconnects.",
    0xB172: "Observation time after a trip: the grid must stay inside the reconnection window for this many "
            "seconds before the inverter reconnects (CEI 0-21 asks for a longer wait after a trip than at "
            "start-up, e.g. 300 s, so the grid can settle first).",
    0xB173: "Like the reconnection observation time, but used after a FREQUENCY trip. 0 = same as the "
            "general reconnection time.",
    0xB174: "Power ramp on (first) connection: after connecting, the inverter raises its output gradually "
            "instead of jumping to full power, so that many inverters starting together do not cause a "
            "step on the grid. Your app shows it as a time in seconds (e.g. 340 s to reach full power).",
    0xB175: "Power ramp after a trip: same as above, used when reconnecting after a protection trip.",
    0xB178: "Enables the gradual power ramps above (1 = on). With it off the inverter goes to full power "
            "right after connecting.",
    0xB2A0: "Response delay applied to the active/reactive power control functions.",
    # --- cosφ(P) curve ---
    0xB179: "cosφ(P) curve: when on, the inverter changes its power factor depending on how much active "
            "power it is producing — at low power it stays at cosφ = 1, at high power it absorbs some "
            "reactive power (cosφ < 1, 'inductive') to counter the voltage rise its own export causes. "
            "Defined by points A, B, C below.",
    0xB17A: "Point A of the cosφ(P) curve: active power, in ‰ of rated power (e.g. 200 = 20 %).",
    0xB17B: "Point A of the cosφ(P) curve: power factor to use at that power (1 = no reactive power; "
            "negative values = the other direction).",
    0xB17C: "Point B of the cosφ(P) curve: active power, in ‰ of rated power.",
    0xB17D: "Point B of the cosφ(P) curve: power factor at that power.",
    0xB17E: "Point C of the cosφ(P) curve: active power, in ‰ of rated power (usually 1000 = full power).",
    0xB17F: "Point C of the cosφ(P) curve: power factor at that power (e.g. 0.90 at full power).",
    0xB180: "The cosφ(P) curve is used only when the grid voltage is ABOVE this value (lock-in voltage), so "
            "reactive power is absorbed only when the voltage is actually high.",
    0xB181: "The cosφ(P) curve is released when the voltage falls BELOW this value (lock-out voltage).",
    0xB182: "The cosφ(P) curve is also released when the active power falls below this value (‰ of rated).",
    0xB1C6: "Response time constant of the cosφ(P) curve: how quickly the inverter follows the curve "
            "(a filter, to avoid oscillations). Read-only here: non-linear encoding.",
    # --- Q(U) curve ---
    0xB190: "Q(U) curve: when on, the inverter exchanges reactive power depending on the grid VOLTAGE — it "
            "supplies reactive power when the voltage is low and absorbs it when the voltage is high, "
            "helping to keep the voltage in range. Defined by V1..V4 below.",
    0xB191: "The Q(U) curve is activated only when the active power is ABOVE this value (‰ of rated).",
    0xB192: "The Q(U) curve is deactivated when the active power falls BELOW this value (‰ of rated).",
    0xB193: "Q(U) point V1: lowest voltage of the curve. At or below it the inverter supplies the reactive "
            "power set in 'V1 reactive'.",
    0xB194: "Q(U) point V1: reactive power at V1, in ‰ of rated power (positive = supplied / capacitive).",
    0xB195: "Q(U) point V2: start of the dead band — between V2 and V3 no reactive power is exchanged.",
    0xB196: "Q(U) point V2: reactive power at V2 (normally 0).",
    0xB197: "Q(U) point V3: end of the dead band; above it the inverter starts absorbing reactive power.",
    0xB198: "Q(U) point V3: reactive power at V3 (normally 0).",
    0xB199: "Q(U) point V4: highest voltage of the curve. At or above it the inverter absorbs the reactive "
            "power set in 'V4 reactive'.",
    0xB19A: "Q(U) point V4: reactive power at V4, in ‰ of rated power (negative = absorbed / inductive).",
    0xB19C: "Response time constant of the Q(U) curve (filter against oscillations). Read-only here: "
            "non-linear encoding.",
    # --- P(U) curve ---
    0xB1A0: "P(U) curve (volt-watt): when on, the inverter REDUCES its active power when the grid voltage "
            "gets high, instead of tripping on overvoltage. Defined by V1..V4 below; usually the first "
            "points are at 100 % and the last one cuts production down.",
    0xB1A2: "P(U) point V1: voltage of the first point.",
    0xB1A3: "P(U) point V1: allowed active power at V1, in ‰ of rated (1000 = 100 %).",
    0xB1A4: "P(U) point V2: voltage of the second point.",
    0xB1A5: "P(U) point V2: allowed active power at V2, in ‰ of rated.",
    0xB1A6: "P(U) point V3: voltage where the reduction starts (e.g. 251 V).",
    0xB1A7: "P(U) point V3: allowed active power at V3, in ‰ of rated (usually still 1000 = 100 %).",
    0xB1A8: "P(U) point V4: voltage where the reduction reaches its maximum (e.g. 256 V).",
    0xB1A9: "P(U) point V4: allowed active power at V4, in ‰ of rated. Negative values mean the inverter "
            "may even absorb power (e.g. charge the battery) at that voltage.",
    0xB1C8: "Response time constant of the P(U) curve (how fast the power follows the voltage). Read-only "
            "here: non-linear encoding; your app shows ∞ when the filter is off.",
    # --- F(P) curve ---
    0xB183: "F(P) curve (frequency-watt): when on, the inverter reduces its active power when the grid "
            "frequency rises (too much generation on the grid) and — with a battery — can increase it when "
            "the frequency drops. Required by CEI 0-21 so that PV helps stabilise the grid frequency "
            "instead of tripping.",
    0xB184: "F(P): frequency where power reduction STARTS (CEI 0-21: 50.2 Hz).",
    0xB185: "F(P): frequency where power increase starts when the frequency drops (CEI 0-21: 49.8 Hz).",
    0xB186: "F(P): overfrequency end point of the curve.",
    0xB187: "F(P): underfrequency end point of the curve.",
    0xB188: "F(P): after the frequency is back to normal, how long it must stay inside the stop window "
            "before the inverter restores normal power.",
    0xB189: "F(P) stop window, upper limit: the frequency must be below this to end the reduction "
            "(CEI 0-21: 50.1 Hz).",
    0xB18A: "F(P) stop window, lower limit: the frequency must be above this to end the increase "
            "(CEI 0-21: 49.9 Hz).",
    0xB18B: "F(P): ramp used to go back to normal power after the event (shown in seconds by the app).",
    0xB18C: "F(P) internal configuration bits. Read-only: it is a bit field, not a number.",
    0xB18D: "F(P) overfrequency slope: how much active power is cut per Hz above the start point, in ‰ "
            "(e.g. 400 = 40 % of the power per Hz).",
    0xB18E: "F(P) underfrequency slope: how much active power is added per Hz below the start point, in ‰.",
    0xB18F: "F(P) recovery slope: how fast the power comes back after the event, in ‰ (per minute).",
    # --- ride-through ---
    0xB1BB: "Enables the low/high voltage ride-through functions below (bit field: LVRT and HVRT). "
            "Read-only here.",
    0xB1BC: "LVRT (low voltage ride-through): during a short voltage dip the inverter stays connected "
            "instead of tripping, as long as the voltage stays above the LVRT curve. This is where the "
            "curve starts (e.g. 195.5 V).",
    0xB1BD: "LVRT: lowest voltage of the curve (e.g. 39.1 V); below it the inverter may trip immediately.",
    0xB1BE: "LVRT: how long the inverter rides through at the start voltage (e.g. 3.2 s). Read-only here.",
    0xB1BF: "LVRT: how long the inverter rides through at the lowest voltage (e.g. 0.4 s). Read-only here.",
    0xB1C0: "LVRT: voltage below which the ride-through mode starts.",
    0xB1C1: "HVRT (high voltage ride-through): the same as LVRT for short voltage SPIKES. This is where the "
            "curve starts.",
    0xB1C2: "HVRT: highest voltage of the curve.",
    0xB1C3: "HVRT: how long the inverter rides through at the start voltage. Read-only here.",
    0xB1C4: "HVRT: how long the inverter rides through at the highest voltage. Read-only here.",
    0xB1C5: "HVRT: voltage above which the ride-through mode starts.",
    # --- PQ ---
    0xB1D6: "PQ curve: when on, limits the reactive power the inverter may exchange depending on its active "
            "power (a 'capability' curve set by the grid operator). Usually off in Italy.",
}
for _r, _what in ((0xB1D7, "generation P1"), (0xB1D9, "generation P2"), (0xB1DB, "generation P3"),
                  (0xB1DD, "load P1"), (0xB1DF, "load P2"), (0xB1E1, "load P3")):
    REG_HELP[_r] = (f"PQ curve point ({_what}): active power, in ‰ of rated power. 'Generation' points "
                    "apply while exporting, 'load' points (negative) while the battery charges.")
    REG_HELP[_r + 1] = (f"PQ curve point ({_what.replace('P', 'Q')}): maximum reactive power at that active "
                        "power, in ‰ of rated power.")
