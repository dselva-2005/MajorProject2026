"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";

const VehicleMap = dynamic(
  () => import("./components/VehicleMap"),
  {
    ssr: false,
  }
);

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

interface JourneyResponse {
  vehicle_id: string;

  total_distance_meters: number;
  total_distance_km: number;

  national_tolled_distance_meters: number;
  national_tolled_distance_km: number;

  state_tolled_distance_meters: number;
  state_tolled_distance_km: number;

  other_tolled_distance_meters: number;
  other_tolled_distance_km: number;

  total_tolled_distance_meters: number;
  total_tolled_distance_km: number;

  processed_segments: ProcessedSegment[];
}

interface ErrorResponse {
  error?: string;
}

const VEHICLE_ID = "VH001";
const API_URL = "http://localhost:8000";

const POLL_INTERVAL = 2000;

export default function Home() {
  const [journey, setJourney] =
    useState<JourneyResponse | null>(null);

  const [initialLoading, setInitialLoading] =
    useState(true);

  const [connectionError, setConnectionError] =
    useState<string | null>(null);

  const [lastUpdated, setLastUpdated] =
    useState<Date | null>(null);

  useEffect(() => {
    let isMounted = true;

    async function fetchJourney() {
      try {
        const response = await fetch(
          `${API_URL}/api/vehicles/${VEHICLE_ID}/toll-distance/`,
          {
            method: "GET",
            cache: "no-store",
          }
        );

        /*
         * ==================================================
         * 404 - NO GPS DATA YET
         * ==================================================
         *
         * This is a normal state for the simulator/frontend.
         *
         * Do NOT throw an Error here.
         *
         * The simulator may not have sent its first GPS
         * point yet, so simply wait for the next poll.
         */

        if (response.status === 404) {
          if (!isMounted) {
            return;
          }

          setConnectionError(null);

          return;
        }

        /*
         * ==================================================
         * OTHER HTTP ERRORS
         * ==================================================
         */

        if (!response.ok) {
          let message =
            `Backend returned HTTP ${response.status}.`;

          try {
            const errorData: ErrorResponse =
              await response.json();

            if (
              errorData &&
              typeof errorData.error === "string" &&
              errorData.error.trim() !== ""
            ) {
              message = errorData.error;
            }
          } catch {
            /*
             * The response was not JSON.
             *
             * Keep the generic HTTP error message.
             */
          }

          if (!isMounted) {
            return;
          }

          /*
           * IMPORTANT:
           *
           * Do not throw the error.
           *
           * A polling request is allowed to fail.
           * The frontend should continue showing the
           * last successfully received journey.
           */

          setConnectionError(message);

          return;
        }

        /*
         * ==================================================
         * PARSE JSON
         * ==================================================
         */

        let data: unknown;

        try {
          data = await response.json();
        } catch {
          if (!isMounted) {
            return;
          }

          setConnectionError(
            "Backend returned an invalid response."
          );

          return;
        }

        /*
         * ==================================================
         * BASIC RESPONSE VALIDATION
         * ==================================================
         */

        if (
          !data ||
          typeof data !== "object"
        ) {
          if (!isMounted) {
            return;
          }

          setConnectionError(
            "Backend returned an invalid journey response."
          );

          return;
        }

        const journeyData =
          data as Partial<JourneyResponse>;

        if (
          typeof journeyData.vehicle_id !== "string"
        ) {
          if (!isMounted) {
            return;
          }

          setConnectionError(
            "Journey response is missing vehicle information."
          );

          return;
        }

        if (
          !Array.isArray(
            journeyData.processed_segments
          )
        ) {
          if (!isMounted) {
            return;
          }

          setConnectionError(
            "Journey response contains invalid processed segments."
          );

          return;
        }

        /*
         * ==================================================
         * VALID RESPONSE
         * ==================================================
         */

        if (!isMounted) {
          return;
        }

        setJourney(
          journeyData as JourneyResponse
        );

        setConnectionError(null);

        setLastUpdated(new Date());

      } catch (error) {

        /*
         * ==================================================
         * NETWORK / UNEXPECTED ERROR
         * ==================================================
         *
         * fetch() can fail if Django is unreachable,
         * the connection is interrupted, etc.
         *
         * Again, do NOT crash the page.
         */

        if (!isMounted) {
          return;
        }

        console.error(
          "Journey polling failed:",
          error
        );

        setConnectionError(
          error instanceof Error
            ? error.message
            : "Unable to connect to backend."
        );

      } finally {

        if (isMounted) {
          setInitialLoading(false);
        }
      }
    }

    /*
     * ======================================================
     * INITIAL REQUEST
     * ======================================================
     */

    fetchJourney();

    /*
     * ======================================================
     * POLLING
     * ======================================================
     */

    const interval = setInterval(
      fetchJourney,
      POLL_INTERVAL
    );

    /*
     * ======================================================
     * CLEANUP
     * ======================================================
     */

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  /*
   * ========================================================
   * INITIAL LOADING
   * ========================================================
   *
   * Only display loading while we have never received
   * anything from the backend.
   */

  if (
    initialLoading &&
    journey === null
  ) {
    return (
      <main className="flex h-screen w-screen items-center justify-center">
        <div className="text-center">

          <p className="text-lg font-medium">
            Loading processed vehicle journey...
          </p>

          <p className="mt-2 text-sm text-gray-500">
            Waiting for vehicle GPS data...
          </p>

        </div>
      </main>
    );
  }

  /*
   * ========================================================
   * NO JOURNEY YET
   * ========================================================
   *
   * This happens when:
   *
   * - simulator has not started
   * - vehicle has no GPS points
   * - backend returned 404
   *
   * This is NOT an error.
   */

  if (journey === null) {
    return (
      <main className="flex h-screen w-screen items-center justify-center">
        <div className="max-w-md text-center">

          <p className="text-lg font-medium">
            Waiting for vehicle data...
          </p>

          <p className="mt-2 text-sm text-gray-500">
            Vehicle {VEHICLE_ID} has not produced
            processed journey data yet.
          </p>

          <p className="mt-3 text-sm text-gray-400">
            The frontend will automatically retry.
          </p>

        </div>
      </main>
    );
  }

  /*
   * ========================================================
   * VALID JOURNEY
   * ========================================================
   */

  return (
    <main className="relative h-screen w-screen">

      <VehicleMap
        vehicleId={journey.vehicle_id}
        segments={journey.processed_segments}
      />

      {/* ================================================== */}
      {/* VEHICLE INFORMATION */}
      {/* ================================================== */}

      <div className="absolute left-4 top-4 z-[1000] rounded-lg bg-white p-4 shadow-lg">

        <h2 className="text-lg font-semibold">
          Vehicle {journey.vehicle_id}
        </h2>

        <div className="mt-2 text-sm">

          <p>
            <strong>Total distance:</strong>{" "}
            {Number.isFinite(
              journey.total_distance_km
            )
              ? journey.total_distance_km.toFixed(3)
              : "N/A"}{" "}
            km
          </p>

          <p>
            <strong>Toll distance:</strong>{" "}
            {Number.isFinite(
              journey.total_tolled_distance_km
            )
              ? journey.total_tolled_distance_km.toFixed(3)
              : "N/A"}{" "}
            km
          </p>

          <p>
            <strong>National:</strong>{" "}
            {Number.isFinite(
              journey.national_tolled_distance_km
            )
              ? journey.national_tolled_distance_km.toFixed(3)
              : "N/A"}{" "}
            km
          </p>

          <p>
            <strong>State:</strong>{" "}
            {Number.isFinite(
              journey.state_tolled_distance_km
            )
              ? journey.state_tolled_distance_km.toFixed(3)
              : "N/A"}{" "}
            km
          </p>

          <p>
            <strong>Other:</strong>{" "}
            {Number.isFinite(
              journey.other_tolled_distance_km
            )
              ? journey.other_tolled_distance_km.toFixed(3)
              : "N/A"}{" "}
            km
          </p>

        </div>

        {/* ================================================= */}
        {/* STATUS */}
        {/* ================================================= */}

        <div className="mt-3 border-t pt-2">

          {connectionError ? (
            <div>

              <p className="text-xs font-medium text-orange-600">
                Temporarily unable to update
              </p>

              <p className="mt-1 max-w-xs text-xs text-gray-500">
                {connectionError}
              </p>

              <p className="mt-1 text-xs text-gray-400">
                Showing the last successful data.
                Retrying automatically...
              </p>

            </div>
          ) : (
            <p className="text-xs text-green-600">
              Backend connected
            </p>
          )}

          {lastUpdated && (
            <p className="mt-1 text-xs text-gray-400">
              Last successful update:{" "}
              {lastUpdated.toLocaleTimeString()}
            </p>
          )}

        </div>

      </div>

    </main>
  );
}
