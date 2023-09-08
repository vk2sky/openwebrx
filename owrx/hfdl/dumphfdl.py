from pycsdr.modules import ExecModule
from pycsdr.types import Format
from owrx.aeronautical import AirplaneLocation, AcarsProcessor, IcaoSource
from owrx.map import Map, Source


class HfdlAirplaneLocation(AirplaneLocation):
    pass


class HfdlSource(Source):
    def __init__(self, flight):
        self.flight = flight

    def getKey(self) -> str:
        return "hfdl:{}".format(self.flight)

    def __dict__(self):
        return {"flight": self.flight}


class DumpHFDLModule(ExecModule):
    def __init__(self):
        super().__init__(
            Format.COMPLEX_FLOAT,
            Format.CHAR,
            [
                "dumphfdl",
                "--iq-file", "-",
                "--sample-format", "CF32",
                "--sample-rate", "12000",
                "--output", "decoded:json:file:path=-",
                "0",
            ],
            flushSize=50000,
        )


class HFDLMessageParser(AcarsProcessor):
    def __init__(self):
        super().__init__("HFDL")

    def process(self, line):
        msg = super().process(line)
        if msg is not None:
            payload = msg["hfdl"]
            if "lpdu" in payload:
                lpdu = payload["lpdu"]
                icao = lpdu["src"]["ac_info"]["icao"] if "ac_info" in lpdu["src"] else None
                if lpdu["type"]["id"] in [13, 29]:
                    hfnpdu = lpdu["hfnpdu"]
                    if hfnpdu["type"]["id"] == 209:
                        # performance data
                        self.processPosition(hfnpdu, icao)
                    elif hfnpdu["type"]["id"] == 255:
                        # enveloped data
                        if "acars" in hfnpdu:
                            self.processAcars(hfnpdu["acars"], icao)
                elif lpdu["type"]["id"] in [79, 143, 191]:
                    if "ac_info" in lpdu:
                        icao = lpdu["ac_info"]["icao"]
                    self.processPosition(lpdu["hfnpdu"], icao)

        return msg

    def processPosition(self, hfnpdu, icao=None):
        if "pos" in hfnpdu:
            pos = hfnpdu["pos"]
            if abs(pos['lat']) <= 90 and abs(pos['lon']) <= 180:
                msg = {
                    "lat": pos["lat"],
                    "lon": pos["lon"],
                    "flight": hfnpdu["flight_id"]
                }
                if icao is None:
                    source = HfdlSource(hfnpdu["flight_id"])
                else:
                    source = IcaoSource(icao, humanReadable=hfnpdu["flight_id"])
                Map.getSharedInstance().updateLocation(source, HfdlAirplaneLocation(msg), "HFDL")
