"""GPX and CSV writers for downloaded tracks.

Both use only the standard library. A "track" here is a list of
:class:`~rgm3800.waypoint.Waypoint`; the writers accept a list of such tracks
so that multiple stored tracks map onto multiple GPX ``<trk>`` segments.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from xml.dom import minidom

from .waypoint import Waypoint

GPX_NS = "http://www.topografix.com/GPX/1/1"


def build_gpx(tracks: list[list[Waypoint]], *, creator: str = "rgm3800-tool"
              ) -> str:
    ET.register_namespace("", GPX_NS)
    gpx = ET.Element(f"{{{GPX_NS}}}gpx", {"version": "1.1", "creator": creator})

    for points in tracks:
        if not points:
            continue
        trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
        seg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")
        for wp in points:
            trkpt = ET.SubElement(
                seg,
                f"{{{GPX_NS}}}trkpt",
                {"lat": f"{wp.lat:.6f}", "lon": f"{wp.lon:.6f}"},
            )
            if wp.alt is not None:
                ET.SubElement(trkpt, f"{{{GPX_NS}}}ele").text = f"{wp.alt:.1f}"
            dt = wp.datetime
            if dt is not None:
                ET.SubElement(trkpt, f"{{{GPX_NS}}}time").text = (
                    dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            if wp.fmt >= 4:
                if wp.num_sats:
                    ET.SubElement(trkpt, f"{{{GPX_NS}}}sat").text = str(wp.num_sats)
                if wp.hdop is not None:
                    ET.SubElement(trkpt, f"{{{GPX_NS}}}hdop").text = f"{wp.hdop:.1f}"
                if wp.vdop is not None:
                    ET.SubElement(trkpt, f"{{{GPX_NS}}}vdop").text = f"{wp.vdop:.1f}"
                if wp.pdop is not None:
                    ET.SubElement(trkpt, f"{{{GPX_NS}}}pdop").text = f"{wp.pdop:.1f}"

    rough = ET.tostring(gpx, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    # minidom emits its own header; keep a single clean one.
    body = "\n".join(line for line in pretty.splitlines() if line.strip())
    if body.startswith("<?xml"):
        body = body.split("\n", 1)[1]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def build_csv(tracks: list[list[Waypoint]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "track", "datetime_utc", "latitude", "longitude",
        "altitude_m", "speed_kmh", "distance_m", "satellites",
        "hdop", "pdop", "vdop",
    ])
    for idx, points in enumerate(tracks):
        for wp in points:
            dt = wp.datetime
            writer.writerow([
                idx,
                dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "",
                f"{wp.lat:.6f}",
                f"{wp.lon:.6f}",
                "" if wp.alt is None else f"{wp.alt:.1f}",
                "" if wp.vel is None else f"{wp.vel:.2f}",
                "" if wp.dist is None else wp.dist,
                wp.num_sats if wp.fmt >= 4 else "",
                "" if wp.hdop is None else f"{wp.hdop:.1f}",
                "" if wp.pdop is None else f"{wp.pdop:.1f}",
                "" if wp.vdop is None else f"{wp.vdop:.1f}",
            ])
    return out.getvalue()


KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"


def build_kml(tracks: list[list[Waypoint]], names: list[str] | None = None,
              *, document_name: str = "RGM-3800 tracks") -> str:
    """Build a KML using ``gx:Track`` so each point keeps its timestamp.

    A ``gx:Track`` pairs a ``<when>`` time with a ``<gx:coord>`` (``lon lat
    alt``) for every point, which Google Earth can animate on its time slider.
    Points without a known time get an empty ``<when/>`` (a valid time gap).
    """
    ET.register_namespace("", KML_NS)
    ET.register_namespace("gx", GX_NS)
    kml = ET.Element(f"{{{KML_NS}}}kml")
    doc = ET.SubElement(kml, f"{{{KML_NS}}}Document")
    ET.SubElement(doc, f"{{{KML_NS}}}name").text = document_name

    for idx, points in enumerate(tracks):
        if not points:
            continue
        placemark = ET.SubElement(doc, f"{{{KML_NS}}}Placemark")
        label = names[idx] if names and idx < len(names) else f"Track {idx}"
        ET.SubElement(placemark, f"{{{KML_NS}}}name").text = label
        track = ET.SubElement(placemark, f"{{{GX_NS}}}Track")
        ET.SubElement(track, f"{{{KML_NS}}}altitudeMode").text = "clampToGround"
        for wp in points:
            dt = wp.datetime
            ET.SubElement(track, f"{{{KML_NS}}}when").text = (
                dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else ""
            )
        for wp in points:
            alt = wp.alt if wp.alt is not None else 0.0
            ET.SubElement(track, f"{{{GX_NS}}}coord").text = (
                f"{wp.lon:.6f} {wp.lat:.6f} {alt:.1f}"
            )

    rough = ET.tostring(kml, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    body = "\n".join(line for line in pretty.splitlines() if line.strip())
    if body.startswith("<?xml"):
        body = body.split("\n", 1)[1]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def write_kml(path: str, tracks: list[list[Waypoint]],
              names: list[str] | None = None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_kml(tracks, names))


def write_gpx(path: str, tracks: list[list[Waypoint]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_gpx(tracks))


def write_csv(path: str, tracks: list[list[Waypoint]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(build_csv(tracks))
