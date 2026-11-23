# Changelog

Changes in this fork since [bosch-thermostat/home-assistant-bosch-custom-component](https://github.com/bosch-thermostat/home-assistant-bosch-custom-component) `master` (`7a27f76`).

## [Unreleased]

### Added

- Virtual heat-pump diagnostics (HTTP, experimental, disabled by default). See [docs/virtual-heatpump-sensors.md](docs/virtual-heatpump-sensors.md).
- SPF today / last 24h / month / year sensors: `(produced + eheater) / consumed` (lists or HA stored statistics).
- HC curve/supply sensors on each heating-circuit device (disabled by default; only paths the heat pump exports). See [docs/hc-curve-sensors.md](docs/hc-curve-sensors.md).
- Pool climate and Pool **Heating** switch (`switch.pool_heating`) when the gateway exposes pool paths.
- Derived `recording:renvironmentexternal` and HDH `recording:rheatingdegreehoursexternal`. See [docs/heating-degree-hours.md](docs/heating-degree-hours.md).
- `bosch.backfill_environment_energy`, `bosch.repair_energy_statistics`, `bosch.clear_energy_statistics`.
- Manual service `bosch.repair_recording_sums` (dry-run by default) to recompute cumulative sums for IVT energy recordings while keeping hourly states. Writes a config-dir backup before any real run. Never runs automatically; after a real run, use `bosch.backfill_environment_energy` for environment.
- Configure: show last-hour recording values; energy stats repair days.
- Fetch recordings by date range.

### Changed

- New statistic API keeps recording entity state at `-17`; graphs use `recording:…external`.

### Fixed

- Pool climate writes use gateway `allowedValues` and raise on failure (no silent `raw_put` swallow).
- COP / SPF today and last 24h: merged yesterday+today hour rows live in a coordinator store (library `RecordingSensor.state` is read-only, so assigning merged state never worked). Hourly pull order is yesterday → today → LTS push so today’s rows keep flowing into stored statistics.
- COP / SPF last 24h use HA hourly statistics for `[current hour − 24h, current hour)` so they no longer equal “today” when the gateway list is today-only; recording day pulls use noon to avoid the client `hour < 1` rewind.
- COP month / year use produced / compressor (same as today); month/year totals fall back to HA stored statistics when the gateway has no period scalars.
- Recording LTS **upsert**: when zero-energy hours are omitted, the cumulative `sum` no longer resets after a gap. The upsert uses the latest in-window row strictly before the change, otherwise a gap-safe recorder lookup that walks history backward. The same anchor logic applies to derived `renvironment` and HDH hourly tails and to `bosch.repair_recording_sums`.
- Recording LTS **upsert**: supports late or corrected hourly values by rewriting changed rows and recomputing cumulative `sum` from the first change. This avoids permanently retaining an incorrect first sample.
- Energy `0` kWh hours stay omitted until Bosch revises them; temperature recordings **keep real 0 °C** hours (client used to drop them) via day re-fetch / `recording_temp_fill`.
- Recording entities set `last_reset` only when `state_class` is `total` (HA rejects it otherwise, e.g. temperature recordings); temperature recording fetches log and skip bad payloads instead of failing the hourly update.
- Virtual snapshot cache uses `TimestampDataUpdateCoordinator` so `last_update_success_time` advances each refresh; without that the cache key stayed frozen and diagnostic sensors stopped updating after setup.
