#!/usr/bin/env python3

import argparse
import datetime
import pathlib
from pathlib import Path
from typing import Optional

from gopro_overlay import ffmpeg
from gopro_overlay.arguments import BBoxArgs
from gopro_overlay.common import smart_open
from gopro_overlay.counter import ReasonCounter
from gopro_overlay.framemeta import framemeta_from
from gopro_overlay.framemeta_gpx import framemeta_to_gpx
from gopro_overlay.gpmd import GPS_FIXED_VALUES
from gopro_overlay.gpmd_visitors_gps import WorstOfGPSLockFilter, GPSLockTracker, GPSReportingFilter, GPSDOPFilter, GPSMaxSpeedFilter, GPSBBoxFilter, NullGPSLockFilter
from gopro_overlay.log import log, fatal
from gopro_overlay.units import units

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Convert GoPro MP4 file to GPX")

    parser.add_argument("--every", default=0, type=int, help="Output a point every 'n' seconds. Default is output all points (usually 20/s)")
    parser.add_argument("--only-locked", action="store_true", help="Only output points where GPS is locked")

    parser.add_argument("--gps-dop-max", type=float, default=10, help="Max DOP - Points with greater DOP will be considered 'Not Locked'")
    parser.add_argument("--gps-speed-max", type=float, default=60, help="Max GPS Speed - Points with greater speed will be considered 'Not Locked'")
    parser.add_argument("--gps-speed-max-units", default="kph", help="Units for --gps-speed-max")
    parser.add_argument("--gps-bbox-lon-lat", action=BBoxArgs, help="Define GPS Bounding Box, anything outside will be considered 'Not Locked' - minlon,minlat,maxlon,maxlat")

    parser.add_argument("input", type=pathlib.Path, help="Input MP4 file")
    parser.add_argument("output", type=pathlib.Path, nargs="?", default="-", help="Output GPX file (default stdout)")

    args = parser.parse_args()

    source = args.input

    if not source.exists():
        fatal(f"{source}: No such file or directory")

    log(f"Loading GoPro {source}")

    if args.gps_bbox_lon_lat:
        bbox_filter = GPSBBoxFilter(args.gps_bbox_lon_lat)
    else:
        bbox_filter = NullGPSLockFilter()

    counter = ReasonCounter()

    stream_info = ffmpeg.find_streams(source)
    fm = framemeta_from(
        source,
        metameta=stream_info.meta,
        units=units,
        gps_lock_filter=WorstOfGPSLockFilter(
            GPSReportingFilter(GPSLockTracker(), rejected=counter.inc("Heuristics")),
            GPSReportingFilter(bbox_filter, rejected=counter.inc("Outside BBox")),
            GPSReportingFilter(GPSDOPFilter(args.gps_dop_max), rejected=counter.inc(f"DOP > {args.gps_dop_max}")),
            GPSReportingFilter(GPSMaxSpeedFilter(units.Quantity(args.gps_speed_max, args.gps_speed_max_units).to("mps").m), rejected=counter.inc(f"Speed > {args.gps_speed_max} {args.gps_speed_max_units}"))
        )
    )

    if counter.total() > 0:
        log(f"Note: {counter.total()} GoPro GPS readings were mapped to 'NO_LOCK', for the following reasons:")
        [log(f"* {k} -> {v}") for k, v in counter.items()]

    log("Generating GPX")

    locked_2d = lambda e: e.gpsfix in GPS_FIXED_VALUES
    filter_fn = locked_2d if args.only_locked else lambda e: True

    gpx = framemeta_to_gpx(fm, step=datetime.timedelta(seconds=args.every), filter_fn=filter_fn)

    dest: Optional[Path] = args.output

    with smart_open(dest) as f:
        f.write(gpx.to_xml())
