# FermentAI — MASTER ORCHESTRATOR INSTRUCTION
## ScienceClaw Hackathon | NVIDIA ALCHEMI × Formlabs × Decentralized AI Scientists

> Supplements `PLAN.md`. Simulation/ALCHEMI/geometry sections largely restate work already done;
> the new material is the shared contracts (§4), ESP32 firmware (§5), virtual ESP32s (§6),
> sensor interpretation for agents (§9.1–9.2), backend tables/command handling (§13) and the
> complete judge workflow (§14). The pasted source was truncated at the end of §14.

**Instruction type:** Expansion of an existing active development project
**Primary development machine:** macOS
**Primary coding environment:** Claude Code with delegated subagents
**Remote scientific compute:** NVIDIA Brev Linux GPU accessed by SSH
**Physical devices:** Two ESP32-S3-CAM fermentation chambers
**Demonstration network:** Local Wi-Fi, functional without internet
**Presentation length:** 3–5 minutes

---

# 0. YOUR ROLE AS THE ORCHESTRATOR

You are the **principal systems architect and engineering orchestrator** for FermentAI.

You are already working on the NVIDIA ALCHEMI and fermentation simulation components. **Do not discard, overwrite, or restart that work.**

Instead, expand the existing implementation to cover the complete system described below.

You must:

1. Inspect the existing repository, plans, running tasks, and completed simulation work.
2. Identify which parts of this specification already exist.
3. Preserve working code and simulation outputs.
4. Delegate independent workstreams to specialized subagents.
5. Establish shared interfaces before those agents implement dependent systems.
6. Let agents work in parallel where possible.
7. Continuously integrate their work.
8. Execute automated tests and produce a runnable demonstration.
9. Produce one consolidated status report identifying implemented, tested, simulated, and hardware-unverified components.

**Your responsibility is implementation, not merely task planning.**

You have permission to write code, establish development environments, compile software, run local tests, and create project files.

For paid cloud resources, actual physical actuator operation, or physical printing, respect the approval and safety requirements below.

## Critical operating rule

**The current simulation agent is the orchestrator. It owns integration.**

It should not personally implement every module sequentially.

Spawn or delegate to focused subagents, give them explicit ownership boundaries, and integrate their outputs against the same contracts.

Subagents may work independently, but must not invent incompatible telemetry formats, command schemas, units, or network interfaces.

---

# 1. SCIENTIFIC VISION

## Project name

**FermentAI — A Decentralized Network of AI Scientists for Food Fermentation**

## Core research question

Can multiple autonomous AI scientist agents, operating physical fermentation experiments in different kitchens, share information and collectively learn how to produce food with desired properties?

The prototype uses two sourdough fermentation chambers.

Each chamber has its own:

- ESP32-S3-CAM microcontroller
- Camera
- Temperature measurements
- Environmental measurements
- Optional pH observations
- Magnetic door sensor
- Peltier heating/cooling controller
- Decorative motorized chef-hat automaton
- Logical scientist agent
- Independent experiment history

The agents propose experimental conditions, observe fermentation, compare observations with predictions, exchange findings, and propose new experiments.

A human eventually bakes and tastes the food, then supplies sensory feedback.

This feedback becomes evidence for the next experiment.

## Long-term vision

Every home kitchen, restaurant, bakery, and food-production environment becomes a distributed scientific laboratory.

The two physical chambers are a proof of concept for a much larger system.

## Hackathon goals

Demonstrate:

1. Genuine NVIDIA ALCHEMI molecular computations.
2. A useful, separate fermentation simulation model.
3. Two physically independent fermentation experiments.
4. Multiple collaborating scientific agents.
5. Formlabs 3D printing of two hypothesized bread-crumb structures.
6. A working local sensor and control platform.
7. An intuitive iPad dashboard.
8. Judge sensory feedback that causes the agents to propose new experiments and physically animate their chef-hat automata.

The demonstration should be engaging, but must distinguish actual physical measurements, real molecular computations, model predictions, and scripted or simulated examples.

---

# 2. OVERALL SYSTEM ARCHITECTURE

Implement the following architecture:

```text
                 HUMAN FLAVOR REQUEST
                          |
                          v
               FERMENTAI LOCAL BACKEND
                      macOS
                          |
          +---------------+---------------+
          |                               |
          v                               v
    SCIENTIST AGENT A               SCIENTIST AGENT B
    Independent state               Independent state
    Experiment history              Experiment history
    Hypotheses                      Hypotheses
    Data interpretation             Data interpretation
          |                               |
          +--------- SHARED FINDINGS -----+
                          |
                          v
                EXPERIMENT DESIGN
             FERMENTATION SIMULATOR
                          |
              +-----------+-----------+
              |                       |
              v                       v
      PHYSICAL CHAMBER A      PHYSICAL CHAMBER B
         ESP32-S3-CAM           ESP32-S3-CAM
         Local sensors          Local sensors
         Peltier control        Peltier control
         Camera                 Camera
         Chef-hat motor         Chef-hat motor
              |                       |
              +-----------+-----------+
                          |
                          v
                MAC TELEMETRY API
                 SQLITE DATABASE
                          |
                          v
                    IPAD DASHBOARD
                          |
                          v
              HUMAN TASTES ACTUAL BREAD
                          |
                          v
               FREE-TEXT TASTE FEEDBACK
                          |
                          v
             AGENTS DESIGN NEXT EXPERIMENT
                          |
                          v
                MOTORIZED CHEF HATS
```

Separately:

```text
MAC / CLAUDE CODE
       |
       | NVIDIA Brev CLI + SSH
       v
REMOTE NVIDIA GPU
       |
       v
NVIDIA ALCHEMI + AIMNet2
       |
       v
MOLECULAR RESULTS
       |
       | SSH result synchronization
       v
MAC LOCAL SCIENTIFIC DATA CACHE
```

The Mac is the source of truth for project code, simulation outputs, database, APIs, and dashboard.

The remote GPU is used only for actual scientific computing.

**The live hackathon demonstration must not depend on an active Brev instance.**

---

# 3. DELEGATION — CREATE THESE SUBAGENT WORKSTREAMS

Create the following subagent assignments.

Where the orchestration environment supports concurrent subagents, run independent tasks simultaneously.

If a corresponding agent already exists, update its instructions instead of duplicating its work.

| Agent | Ownership | Primary deliverable |
|---|---|---|
| A — NVIDIA Molecular Scientist | `src/fermentai/molecular/` | Genuine ALCHEMI computations and outputs |
| B — Fermentation Model Scientist | `src/fermentai/fermentation/` | Two 12–24-hour fermentation simulations |
| C — ESP32 Firmware Engineer | `firmware/` | Compile-ready firmware for both ESP32s |
| D — Virtual Hardware Engineer | `simulated_devices/` | Two functional ESP32 emulators |
| E — Backend/API Engineer | `src/fermentai/api/`, `contracts/` | Shared schemas, telemetry, commands, database |
| F — Scientist-Agent Engineer | `src/fermentai/agents/` | Independent scientific agents and collaboration |
| G — Formlabs Geometry Engineer | `src/fermentai/geometry/`, `data/stl/` | Two printable crumb STLs |
| H — Dashboard/Demo Engineer | `src/fermentai/dashboard/` | Offline iPad interface and judge interaction |
| I — Integration/QA Engineer | `tests/integration/`, `docs/` | End-to-end testing and demo validation |

The orchestrator may merge compatible roles if concurrency is limited.

## Delegation dependencies

**Immediately:** Assign the Backend/API Engineer to establish the shared contracts.

**In parallel:** Begin NVIDIA molecular work, fermentation modeling, firmware scaffolding, virtual hardware scaffolding, and Formlabs geometry generation.

**After contracts are frozen:** Integrate firmware, virtual hardware, dashboard, scientist agents, and API.

**Continuously:** Run tests and merge incremental working changes.

Do not wait until the NVIDIA simulations finish before beginning ESP32 firmware development.

---

# 4. SHARED CONTRACTS — HIGHEST INTEGRATION PRIORITY

Before parallel implementations diverge, establish canonical versioned schemas under:

```text
contracts/
  telemetry_v1.schema.json
  command_v1.schema.json
  acknowledgment_v1.schema.json
  agent_message_v1.schema.json
  experiment_v1.schema.json
  sensory_feedback_v1.schema.json
  simulation_result_v1.schema.json
  README.md
```

Create Pydantic equivalents in Python and compatible C++ models or serializers for ESP32 firmware.

**Every subagent must use these contracts.**

No subagent may independently rename shared fields without orchestrator approval and a corresponding schema update.

## Required telemetry example

```json
{
  "schema_version": "1.0",
  "device_id": "fermentai-esp32-a",
  "chamber_id": "A",
  "experiment_id": "experiment_001",
  "boot_id": "boot_a001",
  "sequence_number": 120,
  "timestamp_utc": null,
  "timestamp_quality": "unsynced",
  "device_uptime_ms": 120000,
  "chamber_temperature_c": 24.3,
  "starter_temperature_c": 23.9,
  "internal_heatsink_temperature_c": 31.2,
  "external_heatsink_temperature_c": 37.1,
  "relative_humidity_percent": 72.0,
  "pressure_pa": 101325.0,
  "gas_resistance_ohms": 105432.0,
  "ph": null,
  "ph_method": null,
  "starter_height_mm": null,
  "door_open": false,
  "peltier_mode": "heating",
  "peltier_output_fraction": 0.35,
  "temperature_setpoint_c": 25.0,
  "motor_running": false,
  "source": "simulated",
  "quality_flags": []
}
```

This is a **synthetic interface example, not a real physical observation**.

Use null for missing values.

The backend must also store server receipt timestamps, per-reading quality/provenance metadata, and image-derived measurements separately.

Physical device telemetry should identify its source as `measured`, not `simulated`.

For individual readings that originate from different sources, such as manual pH versus sensor temperature, store per-field provenance rather than mislabeling the whole record.

## Clock handling

The ESP32 may not have valid UTC time without internet access.

Therefore:
- Include monotonic uptime.
- Include a boot identifier.
- Include monotonically increasing sequence numbers.
- Allow a null UTC timestamp until local time synchronization is available.
- Let the backend assign receipt timestamps.
- Deduplicate using boot ID and sequence number.
- Never silently invent wall-clock timestamps.

## Required API endpoints

```text
POST /api/v1/telemetry
POST /api/v1/images

GET  /api/v1/chambers/{id}/state
GET  /api/v1/chambers/{id}/desired-control

POST /api/v1/control-ack

POST /api/v1/experiments
POST /api/v1/simulations/run
GET  /api/v1/simulations/{id}

POST /api/v1/taste-feedback

GET  /api/v1/agents/{id}/messages
GET  /api/v1/agents/{id}/state
```

Maintain backward compatibility once the firmware contract has been frozen.

Implement device authentication, bounded request sizes, validation, retry handling, and safe defaults.

---

# 5. AGENT C — ESP32 FIRMWARE ENGINEER

## Mission

**Write complete draft firmware for both real ESP32-S3-CAM devices now, before physical connection.**

Do not wait for the user to assemble or connect the hardware.

Produce actual, compile-ready C++ using PlatformIO and Arduino-ESP32.

Create simulated drivers so firmware logic can be unit-tested without physical sensors.

## Device architecture

Both ESP32s run the same firmware image, with per-device configuration.

```text
firmware/
  platformio.ini
  include/
    board_config.h
    device_config.h
    telemetry_schema.h
    command_schema.h
  src/
    main.cpp
    sensors/
      SensorManager.cpp
      BME680Sensor.cpp
      SHT31Sensor.cpp
      DS18B20Manager.cpp
      PHSensor.cpp
      DoorSensor.cpp
    camera/
      CameraManager.cpp
    control/
      TemperatureController.cpp
      PeltierDriver.cpp
      SafetyManager.cpp
      MotorController.cpp
    network/
      NetworkManager.cpp
      TelemetryClient.cpp
      CommandClient.cpp
    app/
      ChamberStateMachine.cpp
      ExperimentManager.cpp
    simulation/
      MockSensors.cpp
      MockActuators.cpp
  test/
  docs/
    GPIO_MAP.md
    SENSOR_WIRING.md
    BRINGUP.md
    HARDWARE_UNCERTAINTIES.md
```

Build profiles:
- ESP32-S3 hardware target
- Native simulation/unit-test target
- Optional hardware-I/O-disabled firmware profile

**The firmware must compile without the physical devices.**

## 5.1. Critical sensor-reading requirement

Each ESP32 must independently read and validate every sensor physically connected to its chamber.

Do not assume the Mac, another ESP32, or a cloud model can directly read GPIO-level sensor data.

Implement one acquisition service per chamber that gathers data from its sensor drivers and publishes a consistent `ChamberObservation` structure.

The firmware must know:
- Which physical sensor produced each value.
- What scientific quantity that sensor measures.
- Its units.
- Its logical placement or role.
- Its latest value.
- When the reading was collected.
- Whether the reading is valid.
- Whether the sensor is missing or stale.
- Whether calibration is required.

### SENSOR A — DS18B20 temperature probes

**Purpose:** Measure starter temperature and Peltier thermal conditions.

Use established Arduino OneWire and DallasTemperature libraries.

Implement:
- One shared OneWire data bus.
- Discovery of multiple attached probes.
- Reading each probe's unique ROM address.
- Address-to-role mapping.
- Persistent role assignment.
- Nonblocking conversion where practical.
- Measurement timestamps.
- CRC/error detection.
- Disconnected-probe detection.
- Rejection of invalid or stale temperatures.

Logical roles:

```text
starter_temperature
internal_heatsink_temperature
external_heatsink_temperature
```

Use a 4.7 kΩ pull-up from data to 3.3 V for a suitable three-wire configuration.

Do not identify probe roles based only on the order in which probes appear on the bus.

A probe's physical placement matters. The same DS18B20 model can represent dough temperature, chamber temperature, or heatsink temperature depending on where it is attached.

Store that placement explicitly.

**Important:** A generic waterproof probe is not automatically certified for food contact. If the starter probe touches a sample, use a separately designated, non-edible instrumented sample unless food-contact suitability is verified.

### SENSOR B — BME680 environmental sensor

**Purpose:** Measure chamber environmental conditions.

Use I²C.

Read:
- Air temperature, °C
- Relative humidity, %
- Pressure, Pa
- Gas resistance, ohms

Implement initialization, address detection, configured I²C pins, measurement retries, and stale-data detection.

Document effects of sensor heating and warm-up behavior.

**Do not report BME680 gas resistance as CO₂ concentration.**

The gas-resistance reading is a broad response to gases and environmental conditions, not a direct, calibrated CO₂ measurement.

The dashboard may visualize gas resistance alongside modeled CO₂ generation, but they must remain distinct quantities.

### SENSOR C — SHT31 environmental sensor

Provide an optional interchangeable environmental sensor driver.

Read:
- Air temperature
- Relative humidity

The firmware must support either BME680 or SHT31, selected through configuration.

If both are installed, use appropriate I²C addressing and document their different roles.

### SENSOR D — Analog pH electrode interface

Only **one pH sensor is currently available**.

Do not assume both chambers have continuous pH measurements.

Implement pH support as optional per device.

The physical probe requires an appropriate high-impedance interface board.

The ESP32 must read the interface board's conditioned analog output, **not the raw glass electrode directly**.

Before enabling the physical ADC input:
- Verify interface supply requirements.
- Measure output voltage range.
- Verify the ESP32 ADC cannot receive voltage above its allowed input range.
- Configure appropriate analog conditioning.
- Calibrate the ADC and pH conversion.
- Support pH 4 and pH 7 calibration.
- Record calibration date and coefficients.

Publish:
- Raw ADC measurement
- Calibrated voltage
- Estimated pH
- Calibration validity
- Measurement quality
- Measurement method

The permanently immersed probe is experimental and may foul or drift in dough.

Support intermittent manual pH measurements and allow missing pH data.

Do not fabricate a second chamber's pH observations.

### SENSOR E — Magnetic reed switch

**Purpose:** Detect when the chamber door opens or closes.

Use a digital input and suitable pull-up configuration.

Implement:
- Input debouncing
- Logical polarity configuration
- Open/closed status
- Time since last change
- Door-opening count

Do not assume the same reed-switch polarity for every model.

Door events should be logged because they can affect environmental temperature, humidity, and image conditions.

### SENSOR F — ESP32-S3 camera

**Purpose:** Observe sourdough expansion over time.

Use the Espressif camera driver.

Implement:
- Board-specific camera pin configuration
- JPEG image capture
- Configurable resolution
- Configurable capture interval
- Configurable exposure/lighting settings
- Image upload to the Mac backend
- Error recovery
- Memory-aware capture buffers

The camera is outside the jar, looking through the chamber window.

The Mac processes images to estimate starter rise or height.

Do not attempt heavyweight image analysis on the ESP32 for this initial implementation.

Camera capture or uploading must not block local safety control.

Camera failures must not crash temperature regulation.

### SENSOR G — Optional local diagnostics

Also publish:
- Supply/reset diagnostics where available
- Wi-Fi RSSI
- Device uptime
- Free heap
- Last command identifier
- Sensor health flags
- Firmware version

These help debug the physical demonstration.

## 5.2. Per-device configuration

Support:

```text
CHAMBER_ID=A
DEVICE_ID=fermentai-esp32-a
ENV_SENSOR=BME680
PH_SENSOR_ENABLED=true
CAMERA_ENABLED=true
MOTOR_ENABLED=true
```

And separately:

```text
CHAMBER_ID=B
DEVICE_ID=fermentai-esp32-b
ENV_SENSOR=BME680
PH_SENSOR_ENABLED=false
CAMERA_ENABLED=true
MOTOR_ENABLED=true
```

These are example configurations.

Final settings must reflect the actual assembled hardware.

## 5.3. GPIO map — provisional only

Start from the previously proposed assignments:

| Function | Proposed GPIO |
|---|---|
| I²C SDA | GPIO41 |
| I²C SCL | GPIO42 |
| DS18B20 OneWire | GPIO47 |
| Door reed switch | GPIO39 |
| Optional pH ADC | GPIO1 |
| Peltier PWM direction A | GPIO14 |
| Peltier PWM direction B | GPIO21 |
| Peltier driver enable | GPIO40 |
| Motor switch | GPIO38 |

**These GPIO numbers are not confirmed for the exact Aideepen board revision.**

Before claiming the pin map is valid:
1. Locate the exact board schematic or pinout.
2. Identify the actual camera GPIO assignments.
3. Identify flash, PSRAM, SD-card, USB and boot-pin conflicts.
4. Verify pin exposure on the physical board.
5. Check voltage levels and boot behavior.
6. Resolve any conflicts.
7. Record the result in `GPIO_MAP.md`.

Do not hardcode provisional pins throughout the application.

Centralize them in one configuration file.

Compile success is not proof of electrical compatibility.

## 5.4. Local temperature control

Implement modes:

```text
IDLE
HEATING
COOLING
FAULT
```

The Peltier is driven through its dedicated BTS7960.

The ESP32 receives bounded setpoints from its scientist agent through the Mac backend.

**The ESP32, not the LLM, performs the real-time thermal control.**

Implement:
- Safe initialization
- Temperature readback
- Configurable hysteresis or proportional control
- Heating/cooling polarity selection
- PWM limiting
- Dead-time when changing direction
- Sensor-failure response
- Command expiration handling
- Temperature-limit checks
- Fault logging

The hot-side and cold-side heatsink sensors must be monitored where fitted.

The physical Peltier fans must be separately powered and appropriately protected.

Do not control fans by reversing Peltier polarity.

The firmware must not claim that software alone provides adequate thermal safety.

Actual use requires:
- Correctly protected DC branches
- Verified driver logic compatibility
- Independent thermal cutoffs
- Verified fan operation
- Safe thermal separation from combustible XPS
- Safe high-current wiring

**Real high-power outputs remain disabled until commissioning.**

## 5.5. Chef-hat automaton motor

Each ESP32 controls its own N20 motor through a separate 3.3 V-compatible MOSFET switching circuit.

The motor is not powered from the ESP32 GPIO and does not share the Peltier H-bridge outputs.

Implement:

```cpp
startMotor(duration_ms);
stopMotor();
isMotorRunning();
```

Requirements:
- Timed activation
- Independent control from the Peltier
- Automatic timeout
- Maximum permitted duration
- Safe OFF state on reboot
- Command acknowledgment
- No indefinite ON command

The judge-feedback workflow will send an `animate_motor` command after a new experiment is proposed.

Motor activity signals that a scientific agent has received a new assignment.

It does not represent actual automated feeding or cooking.

## 5.6. Networking

Each ESP32 joins a dedicated local Wi-Fi network.

The Mac runs the backend.

Implement:
- Configurable Wi-Fi credentials
- Backend host/IP configuration
- Device authentication
- HTTP telemetry uploads
- Image uploads
- Command polling
- Command acknowledgment
- Reconnection
- Bounded retries
- Local queues
- Network-failure handling

No cloud access should be needed for basic sensing or safe local temperature control.

Never embed cloud LLM credentials in firmware.

## 5.7. Firmware task scheduling

Suggested initial rates:

| Task | Frequency |
|---|---|
| Safety checks | 100–250 ms |
| Temperature control | 1 s |
| DS18B20 acquisition | 1–5 s |
| BME680 acquisition | 3–10 s |
| Door monitoring | 20–100 ms |
| Telemetry upload | 5–15 s |
| Command polling | 1–3 s |
| Camera capture | 30–60 s |
| Motor expiration check | 20–50 ms |

Use nonblocking scheduling.

Do not let image uploads delay safety checks.

## 5.8. Firmware tests

Before hardware arrives, the firmware agent must:

- Compile the ESP32 target.
- Run native control-logic tests.
- Exercise mock temperature probes.
- Test multiple OneWire addresses.
- Verify sensor failure handling.
- Test missing pH.
- Test invalid BME680 readings.
- Test door-switch debouncing.
- Test motor duration limits.
- Test heating-to-cooling transitions.
- Test command expiration and replay protection.
- Produce valid telemetry JSON.
- Document hardware tests still required.

**Deliverable:** Compiled firmware and a complete physical bring-up guide.

---

# 6. AGENT D — VIRTUAL ESP32 ENGINEER

Create two software emulators running on the Mac.

These must communicate with the actual backend using the exact API that the real ESP32s will use.

```text
simulated_devices/
  chamber_A.py
  chamber_B.py
  virtual_thermal_plant.py
  virtual_sensors.py
  virtual_camera.py
  virtual_motor.py
  network_client.py
  fixtures/
  tests/
```

Each virtual device must have:
- Independent temperature
- Independent environmental readings
- Independent experiment state
- Simulated camera images
- Door events
- Motor state
- Peltier state
- Optional pH observations

Use a simple first-order thermal response model.

Do not present this controller test model as a validated physical simulation.

Virtual sensor readings must be tagged `simulated`.

Recorded physical readings shown in replay mode must be tagged `replay`.

## Required virtual-device behavior

1. Boot both virtual ESP32s.
2. Connect to the Mac backend.
3. Send telemetry independently.
4. Receive temperature commands.
5. Simulate thermal responses.
6. Respond to door events.
7. Upload image fixtures.
8. Accept motor commands.
9. Animate motor state for a specified duration.
10. Acknowledge completed commands.

The actual iPad interface must work with these simulated devices before physical hardware integration.

---

# 7. AGENT A — NVIDIA MOLECULAR SCIENTIST

Preserve and continue the current NVIDIA ALCHEMI work.

## Remote compute

Claude Code runs on the Mac.

Use the already installed NVIDIA Brev skill to:
- Authenticate
- Inspect credit balance
- Find a suitable GPU
- Provision an instance when approved
- Connect via SSH
- Execute molecular calculations
- Transfer outputs back to the Mac
- Stop compute when finished

The user has approximately $150 in Brev credits.

Target an initial exploration budget of $40, subject to verifying actual pricing and credit eligibility.

Do not launch expensive work without explicit approval.

## Required real computations

Use:
- NVIDIA ALCHEMI Toolkit
- AIMNet2
- RDKit
- ASE
- CUDA-enabled PyTorch

Begin with an official ALCHEMI example.

Confirm real model inference returns finite energies and forces.

Then perform molecular optimization on:
- Acetic acid + water clusters
- Lactic acid + water clusters

Water counts:
- 1
- 3
- 6

Use multiple seeded initial geometries.

Preserve:
- Molecular structures
- Optimized coordinates
- Energies
- Forces
- Hydrogen-bond geometries
- Convergence results
- Model metadata
- Runtime logs

Create visualizations and a documented comparison.

Do not claim these molecular calculations directly predict fermentation rate, sourness, pH, or crumb morphology.

**Deliverable:** Genuine, cached NVIDIA molecular results usable offline in the demo.

---

# 8. AGENT B — FERMENTATION MODEL SCIENTIST

Build or continue a separate mechanistic sourdough fermentation model.

## Initial physical recipe

Two miniature loaves, approximately 379 g of dough each:

| Ingredient | Per loaf |
|---|---:|
| Bread flour | 200 g |
| Water | 135 g |
| Ripe 100%-hydration starter | 40 g |
| Salt | 4.4 g |

Approximate total hydration: 70.5%, accounting for starter flour and water.

Use identical ingredients for both experimental loaves.

The mother starter is approximately four to five days old and may not yet be mature enough for reliable bread fermentation.

Check for consistent rising activity before the physical bake. Do not assume apparent bubbling proves the starter is ready.

## Experimental goals

**Chamber A:** Produce a relatively mild sourdough.

**Chamber B:** Produce a more acidic, tangier sourdough.

Initial nominal temperature conditions:
- Chamber A: 22°C
- Chamber B: 27°C

These are hypotheses, not guaranteed flavor outcomes.

The scientist agents may propose different bounded temperature schedules based on the model and incoming observations.

Keep flour, hydration, starter inoculation, and initial mixing conditions matched so the effect of the temperature intervention is interpretable.

The anticipated fermentation/proofing window is 12–16 hours, with a 24-hour simulation horizon for exploration.

Agents must account for overproofing risk and cannot assume every temperature schedule is suitable for the full duration.

## Model state variables

Use SciPy `solve_ivp` and model:

- Fermentable substrate
- Yeast biomass proxy
- Lactic acid bacteria biomass proxy
- Lactic acid accumulation
- Acetic acid accumulation
- Cumulative CO₂ generation

Include:
- Substrate-limited growth
- Temperature-dependent microbial activity
- Substrate consumption
- Acid production
- CO₂ generation
- Acid inhibition where justified
- Empirical pH approximation
- Phenomenological dough-expansion model

All assumptions and parameters must be documented.

## Required model outputs

For each experiment:
- Time-series temperatures
- Predicted pH
- Predicted acid concentrations
- Predicted cumulative CO₂ generation
- Predicted rise/expansion
- Uncertainty ranges
- Parameter sensitivity results

Generate CSV, JSON and plots.

Do not calibrate the model against nonexistent physical measurements.

Do not fabricate validation results.

The difference between theoretical CO₂ generation and BME680 gas resistance must be preserved.

## Dynamic experimentation

Support time-varying temperature schedules.

After real sensor observations arrive:
1. Compare observed and predicted trajectories.
2. Compute errors where comparable measurements exist.
3. Identify model discrepancies.
4. Update supported model parameters.
5. Propose new candidate experiments.

Do not claim pH alone uniquely determines microbial populations or individual acid concentrations.

---

# 9. AGENT F — TWO AUTONOMOUS SCIENTIST AGENTS

Create independent scientific agent processes or logical runtimes.

```text
Scientist A:
  Experiment A history
  Sensor observations from chamber A
  Prediction state
  Current hypothesis
  Proposed interventions
  Peer findings

Scientist B:
  Experiment B history
  Sensor observations from chamber B
  Prediction state
  Current hypothesis
  Proposed interventions
  Peer findings
```

The scientist agents run on the Mac.

**The ESP32s are sensor and actuator nodes, not hosts for large language model inference.**

The agents can use a configured Claude-based model when online inference is available.

Provide a deterministic offline fallback when it is not.

A Claude Code subscription and direct Anthropic API access must be treated as potentially different authentication and billing paths. Verify the actual supported method rather than assuming a subscription provides unrestricted API usage.

## 9.1. Sensor interpretation

This section is mandatory.

**Each scientist agent must understand the scientific meaning and limitations of the data arriving from its own ESP32.**

Implement a structured observation adapter rather than passing arbitrary unvalidated JSON directly into an LLM.

For example:

```text
Raw sensor acquisition
        |
        v
Validity and unit checks
        |
        v
Timestamp synchronization
        |
        v
Per-sensor quality assessment
        |
        v
Normalized chamber observation
        |
        v
Comparison with simulation
        |
        v
Scientist agent interpretation
```

The observation adapter must understand:

### Starter temperature

Primary controlled fermentation temperature.

Used to evaluate whether the physical starter is following the selected temperature schedule.

### Chamber air temperature

Environmental temperature surrounding the jar.

Useful for detecting thermal lag between the chamber and the dough.

### Heatsink temperatures

Thermal and equipment safety measurements.

These are not fermentation state measurements.

Unexpected heatsink readings should trigger equipment diagnostics or safety action, not a claim about microbial behavior.

### Humidity

Environmental chamber condition.

Can influence drying and condensation, but does not directly determine acid concentration.

### BME680 gas resistance

A broad environmental gas-response measurement.

Can reveal empirical changes in chamber headspace, but is not a calibrated CO₂ concentration or direct fermentation-rate measurement.

### pH

A measurement of acidity when available and valid.

Because only one physical electrode is available, the other chamber may have no pH data.

A contaminated or drifting electrode must not be treated as reliable evidence.

### Camera-derived starter height

An estimate of visible expansion.

It may be affected by camera movement, jar geometry, lighting, door openings, surface collapse, and gas-retention behavior.

It is not identical to cumulative CO₂ generation.

### Door events

Experimental disturbances that may change chamber conditions.

### Peltier state and temperature setpoint

Control inputs that should be compared against actual temperature response.

### Motor state

Decorative automaton state.

Not a scientific measurement of sourdough fermentation.

## 9.2. Quality-aware decisions

Each scientist agent must reject or down-weight:
- Stale measurements
- Disconnected sensors
- Invalid physical values
- Measurements without valid calibration
- Values from the wrong chamber
- Unidentified sensor placements
- Missing or unsynchronized timestamps
- Simulated data misrepresented as measured
- Camera height estimates with low confidence

Do not silently fill missing physical observations with simulated values.

If the agent lacks a valid pH measurement, it should say so and use its predictions with corresponding uncertainty.

## 9.3. Agent actions

Each scientist agent should be capable of:

1. Reading normalized observations.
2. Comparing them to its simulated expectations.
3. Identifying deviations.
4. Updating hypotheses.
5. Requesting additional measurements.
6. Proposing bounded temperature changes.
7. Publishing findings to the peer agent.
8. Reading and critiquing peer findings.
9. Recommending a next experiment.
10. Explaining the scientific reasoning in natural language.

Represent decisions as structured machine-readable records with human-readable explanations.

An LLM proposal must pass a deterministic validator before becoming a hardware command.

## 9.4. Communication

Create a shared append-only event log.

Message types:

```text
observation_summary
hypothesis
prediction
anomaly
experiment_proposal
peer_critique
experiment_result
model_update
sensory_feedback
```

Each message records:
- Sending agent
- Intended recipient(s)
- Experiment ID
- Timestamp
- Evidence references
- Scientific claim
- Uncertainty
- Proposed action

Test both independent operation and peer communication.

## 9.5. Scientific collaboration benchmark

Compare:
- Two agents working independently.
- Two agents sharing structured evidence.

Use the same simulated experimental budget.

Measure:
- Duplicate experiments avoided
- Prediction error
- Reduction in model uncertainty
- Information gained per proposed experiment

Only report improvements that actually result from executing the benchmark.

A synthetic benchmark must be identified as an in-silico evaluation.

---

# 10. AGENT G — FORMLABS GEOMETRY ENGINEER

Generate two physical models of bread-crumb cross-sections.

Output:

```text
data/stl/crumb_mild.stl
data/stl/crumb_sour.stl
data/stl/crumb_mild_preview.png
data/stl/crumb_sour_preview.png
data/stl/crumb_comparison.png
data/stl/morphology_parameters.json
data/stl/mesh_validation.json
```

The objective is to physically display two hypothesized crumb morphologies using a Formlabs printer.

## Modeling

Use a parameterized stochastic morphology model.

Inputs:
- Slice dimensions
- Pore volume fraction
- Pore size distribution
- Pore shape
- Pore elongation
- Pore clustering
- Structural wall thickness
- Random seed

Produce open-faced models approximately 80 × 80 × 12 mm, adjusting to the available printer.

Prioritize:
- Visually clear air-pocket structures
- Robust connected geometry
- Adequate strut thickness
- Watertight printable mesh
- No trapped uncured resin
- No disconnected floating components

Validate both STL files programmatically.

## Scientific limitation

**Sourness does not uniquely determine crumb geometry.**

Do not falsely claim that AIMNet2 predicts the loaf's air-pocket distribution.

Instead:
- Use the fermentation model's rise and gas-retention assumptions to define exploratory morphology parameters.
- Clearly label the initial geometries as illustrative simulated scenarios.
- Compare them against actual bread cross-section images when available.

The judges should see:
1. A simulated crumb hypothesis.
2. A Formlabs-printed representation.
3. The actual baked loaf's cross-section.

## Formlabs workflow

Investigate the official PreForm API.

Optionally prepare a print scene, orientation and supports.

Do not automatically submit a physical print job without approval.

Standard resin prints must not directly contact edible bread unless an appropriate food-contact workflow has been validated.

---

# 11. AGENT H — IPAD DASHBOARD AND HACKATHON DEMO

Build a polished dashboard served from the Mac.

It must be accessible through an iPad browser on the same local Wi-Fi network.

Use locally bundled frontend resources.

**Do not depend on external JavaScript CDNs, Google Fonts, or cloud hosting for the offline demo.**

## Dashboard View 1 — Live Kitchens

Display two chamber cards.

Each card shows:
- Chamber name
- Camera image
- Temperature setpoint
- Starter temperature
- Chamber air temperature
- Environmental humidity
- Gas resistance
- Available pH measurement
- Starter rise estimate
- Door open/closed
- Peltier state
- Motor state
- Scientist agent status
- Last valid sensor update

If a sensor is missing, show an unavailable state, not an invented value.

## Dashboard View 2 — Digital Twin

Display:
- Predicted and measured temperatures
- Predicted and observed pH where available
- Predicted and observed expansion where available
- Modeled acid accumulation
- Modeled CO₂
- Model uncertainty

Make prediction/measurement provenance obvious.

## Dashboard View 3 — NVIDIA Molecular Explorer

Display:
- Real ALCHEMI molecular structures
- Lactic and acetic acid clusters
- Hydrogen bonds
- Energy summaries
- Model checkpoint information
- Actual GPU computation provenance

The UI must use cached results so no live GPU connection is required.

## Dashboard View 4 — Taste Feedback

Large judge-facing prompt:

**What did the bread taste like?**

Accept free-text descriptions of both loaves.

Example:

"Loaf A was mild and fluffy. Loaf B was much more sour, but it was dense. I want the sourness of B and the airy texture of A."

The submission must cause a genuine application workflow:

1. Store the feedback.
2. Extract comparative sensory preferences.
3. Associate observations with the appropriate loaves.
4. Generate candidate experiments.
5. Have scientist A propose a hypothesis.
6. Have scientist B critique or improve it.
7. Select a proposed next experiment.
8. Send commands to both ESP32s to animate their chef-hat motors.
9. Display command acknowledgments.
10. Show the updated experiment plan.

If cloud LLM inference is unavailable, use a deterministic policy that can handle common descriptions.

Label the reasoning mode as `Online Agent` or `Offline Policy`.

Do not claim a scripted response was generated by a live LLM.

## Dashboard demonstration modes

Provide:

**LIVE**
- Real sensor data
- Real device command acknowledgments
- Actual collected experiments

**REPLAY**
- Recorded earlier experiments with preserved timestamps

**SIMULATION**
- Virtual devices and generated data

The judges should be able to distinguish these modes, even though the interface remains visually cohesive.

---

# 12. LOCAL NETWORK REQUIREMENT

Assume the hackathon Wi-Fi may be unreliable or unavailable.

Preferred topology:

```text
                 LOCAL TRAVEL ROUTER / AP
                       No internet needed
                              |
        +---------------------+-------------------+
        |                     |                   |
        v                     v                   v
      MAC                 ESP32 A              ESP32 B
   Backend/UI             Sensors              Sensors
   Agents                 Peltier              Peltier
   Database               Camera               Camera
                          Motor                Motor
        |
        v
      IPAD
   Dashboard
```

All devices connect to the same local subnet.

If a travel router is unavailable, document a tested alternative using a Mac-hosted network or dedicated access-point device.

Do not rely on an assumed hotspot feature without testing client-to-client connectivity.

For the final demo:
- The iPad opens the Mac's local dashboard.
- The ESP32s post sensor data to the Mac.
- The backend sends commands to each ESP32.
- The local scientist processes share messages.
- Cached molecular and fermentation simulations remain available.
- Taste feedback still works offline.

Live cloud LLM inference may be optional.

Test the entire demo with external internet disconnected.

---

# 13. AGENT E — BACKEND/API AND DATABASE

Implement the local FastAPI application.

Use SQLite initially.

Create tables or equivalent persistent structures for:

```text
devices
chambers
sensor_readings
camera_images
experiment_definitions
simulation_runs
temperature_schedules
hardware_commands
command_acknowledgments
scientist_messages
scientist_hypotheses
sensory_feedback
model_calibrations
molecular_results
```

## Telemetry ingestion

Validate incoming JSON against the canonical contract.

Maintain a separate current state for each chamber.

Do not allow one device's values to overwrite the other's data.

Record:
- Device identity
- Experiment identity
- Sensor identity and role
- Raw value
- Units
- Calibration
- Quality
- Source/provenance
- Device uptime
- Server receipt time

Store physical measurements and simulated values with clear separation.

## Command handling

Support commands such as:

```json
{
  "schema_version": "1.0",
  "command_id": "cmd_001",
  "chamber_id": "A",
  "action": "animate_motor",
  "parameters": {
    "duration_ms": 3000
  },
  "issued_at_utc": "2026-10-31T15:00:00Z",
  "expires_at_utc": "2026-10-31T15:00:10Z"
}
```

This is an illustrative message.

Temperature commands should similarly carry:
- Requested setpoint
- Safe control mode
- Command ID
- Expiration
- Experiment ID

The device must enforce its own hardware constraints.

A command is successful only when acknowledged by the corresponding device.

Do not mark a motor as having physically run just because a server queued its command.

## Control security

Use:
- Device-specific credentials
- Command validation
- Bounded values
- Expiring commands
- Replay protection
- Recorded acknowledgments
- Safe reboot behavior

The LLM must never directly manipulate raw GPIO or H-bridge pins.

It may only propose high-level actions that pass a validator and a local controller.

---

# 14. JUDGE INTERACTION — REQUIRED COMPLETE WORKFLOW

Implement this end-to-end interaction.

## Before the judge arrives

The system displays:
- Two chambers.
- Two temperature histories.
- The two simulated fermentation predictions.
- Available real observations.
- Actual or cached NVIDIA molecular outputs.
- Two printed crumb hypotheses.
- Two sourdough test loaves.

## Judge tasting

The judge tastes loaf A and loaf B.

The operator enters:

"Loaf A tastes mild and has nice texture. Loaf B is more sour, which I prefer, but the crumb is too dense. I'd like a more sour loaf that's still fluffy."

## Scientist response

Scientist A interprets the human preference:

```text
Human preference:
  More sourness
  Lower density
  Preserve good rise
```

Scientist A publishes a new hypothesis.

Scientist B reviews the hypothesis and suggests another experimental condition.

The backend runs a quick candidate comparison using the fermentation model.

The system chooses a proposed next experiment and explains why.

## Physical response

The backend sends:

```text
Chamber A:
  animate_motor(duration=3000 ms)

Chamber B:
  animate_motor(duration=3000 ms)
```

The two ESP32s activate their decorative motorized chef hats and return acknowledgments.

The iPad displays the agents' new experimental plans.

This motor animation represents the agents beginning to plan the next experiments; it is not evidence that automated feeding or baking occurred.

## Offline requirement

The entire loop must function using local software, cached predictions, and a
[source truncated here]
