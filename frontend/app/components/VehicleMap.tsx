"use client";

import "leaflet/dist/leaflet.css";

import L from "leaflet";

import {
  MapContainer,
  TileLayer,
  Polyline,
  Marker,
  Popup,
  useMap,
} from "react-leaflet";

import { useEffect } from "react";

type Coordinate = [number, number];

interface ProcessedSegment {
  edge_index: number;
  way_id: number;

  start_fraction: number;
  end_fraction: number;

  distance_meters: number;
  distance_km: number;

  toll: boolean;

  ref: string;
  category: string;

  start_point: Coordinate;
  end_point: Coordinate;

  start_time: string;
  end_time: string;
}

interface VehicleMapProps {
  vehicleId: string;
  segments: ProcessedSegment[];
}

const vehicleIcon = L.icon({
  iconUrl: "/vehicle.svg",
  iconSize: [40, 40],
  iconAnchor: [20, 20],
  popupAnchor: [0, -20],
});

function VehicleLayers({
  vehicleId,
  segments,
}: VehicleMapProps) {
  const map = useMap();

  /*
   * Build the processed route from JourneySegment.
   *
   * Each segment contains:
   *
   * start_point
   * end_point
   *
   * The frontend therefore no longer uses raw GPS
   * coordinates from VehicleLocation.
   */

  const routeCoordinates: Coordinate[] = [];

  for (const segment of segments) {
    if (routeCoordinates.length === 0) {
      routeCoordinates.push(segment.start_point);
    }

    routeCoordinates.push(segment.end_point);
  }

  /*
   * The last processed point represents the current
   * processed vehicle position.
   */

  const currentPosition =
    routeCoordinates[routeCoordinates.length - 1];

  useEffect(() => {
    if (currentPosition) {
      map.setView(currentPosition);
    }
  }, [currentPosition, map]);

  /*
   * Split the route into toll and non-toll segments.
   *
   * This allows the frontend to visually distinguish
   * tolled portions of the processed journey.
   */

  const polylines = segments.map(
    (segment, index) => {
      const positions: Coordinate[] = [
        segment.start_point,
        segment.end_point,
      ];

      return (
        <Polyline
          key={`${segment.edge_index}-${index}`}
          positions={positions}
          pathOptions={{
            weight: 5,
            opacity: 0.8,
          }}
        />
      );
    }
  );

  return (
    <>
      {polylines}

      {currentPosition && (
        <Marker
          position={currentPosition}
          icon={vehicleIcon}
        >
          <Popup>
            <div>
              <strong>
                Vehicle: {vehicleId}
              </strong>

              <br />

              Latitude:{" "}
              {currentPosition[0].toFixed(6)}

              <br />

              Longitude:{" "}
              {currentPosition[1].toFixed(6)}

              <br />

              Edge:{" "}
              {
                segments[segments.length - 1]
                  .edge_index
              }

              <br />

              Way ID:{" "}
              {
                segments[segments.length - 1]
                  .way_id
              }

              <br />

              Toll:{" "}
              {
                segments[segments.length - 1].toll
                  ? "Yes"
                  : "No"
              }
            </div>
          </Popup>
        </Marker>
      )}
    </>
  );
}

export default function VehicleMap({
  vehicleId,
  segments,
}: VehicleMapProps) {
  if (segments.length === 0) {
    return (
      <div>
        No processed vehicle journey available.
      </div>
    );
  }

  const initialPosition =
    segments[0].start_point;

  return (
    <MapContainer
      center={initialPosition}
      zoom={15}
      style={{
        height: "100vh",
        width: "100vw",
      }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      <VehicleLayers
        vehicleId={vehicleId}
        segments={segments}
      />
    </MapContainer>
  );
}
