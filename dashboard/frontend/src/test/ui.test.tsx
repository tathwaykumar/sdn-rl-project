/** Tests for presentation primitives.
 *
 * The emphasis is on the distinction the whole dashboard rests on: a missing
 * measurement must never render as a number, and mock data must always be
 * labelled as mock.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  Empty, ErrorBox, MetricCard, SourceBadge, fmtAge, fmtBits, fmtBytes,
  fmtDuration, fmtNumber, fmtPercent,
} from "../components/ui";

describe("formatters", () => {
  it("renders an em dash for missing values rather than zero", () => {
    expect(fmtNumber(null)).toBe("—");
    expect(fmtNumber(undefined)).toBe("—");
    expect(fmtBits(null)).toBe("—");
    expect(fmtPercent(null)).toBe("—");
    expect(fmtBytes(null)).toBe("—");
    expect(fmtDuration(null)).toBe("—");
    // Zero is a real measurement and must not be confused with missing.
    expect(fmtNumber(0)).toBe("0.00");
    expect(fmtPercent(0)).toBe("0.0%");
  });

  it("scales bit rates to sensible units", () => {
    expect(fmtBits(5_000_000)).toBe("5.00 Mbit/s");
    expect(fmtBits(2_500)).toBe("2.5 kbit/s");
    expect(fmtBits(900)).toBe("900 bit/s");
    expect(fmtBits(3_000_000_000)).toBe("3.00 Gbit/s");
  });

  it("formats percentages from fractions", () => {
    expect(fmtPercent(0.5)).toBe("50.0%");
    expect(fmtPercent(0.9807, 1)).toBe("98.1%");
  });

  it("describes data age in human terms", () => {
    expect(fmtAge(0.5)).toBe("just now");
    expect(fmtAge(12)).toBe("12s ago");
    expect(fmtAge(120)).toBe("2.0 min ago");
    expect(fmtAge(null)).toBe("age unknown");
  });

  it("formats durations", () => {
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(125)).toBe("2m 5s");
  });
});

describe("SourceBadge", () => {
  it("labels mock data explicitly", () => {
    render(<SourceBadge source="mock" />);
    expect(screen.getByText("MOCK DATA")).toBeInTheDocument();
  });

  it("distinguishes live from recorded data", () => {
    const { rerender } = render(<SourceBadge source="live" />);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    rerender(<SourceBadge source="historical" />);
    expect(screen.getByText("RECORDED")).toBeInTheDocument();
  });

  it("falls back to UNAVAILABLE for an unknown or missing source", () => {
    render(<SourceBadge source={undefined} />);
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
  });

  it("marks stale data", () => {
    render(<SourceBadge source="live" age={30} stale />);
    expect(screen.getByText(/stale/)).toBeInTheDocument();
  });
});

describe("MetricCard", () => {
  it("shows the unit and the measurement kind", () => {
    render(
      <MetricCard
        label="Throughput" value="5.00" unit="Mbit/s"
        kind="interval_rate" source="live" sub="from switch counters"
      />,
    );
    expect(screen.getByText("Throughput")).toBeInTheDocument();
    expect(screen.getByText("Mbit/s")).toBeInTheDocument();
    expect(screen.getByText("interval")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("marks instantaneous readings differently from interval aggregates", () => {
    render(<MetricCard label="Occupancy" value="40" kind="instantaneous" />);
    expect(screen.getByText("instant")).toBeInTheDocument();
  });
});

describe("states", () => {
  it("renders an empty state with guidance", () => {
    render(<Empty title="No flow-table data" detail="Start an experiment." />);
    expect(screen.getByText("No flow-table data")).toBeInTheDocument();
    expect(screen.getByText("Start an experiment.")).toBeInTheDocument();
  });

  it("renders an error with a retry affordance", () => {
    render(<ErrorBox message="backend unreachable" onRetry={() => {}} />);
    expect(screen.getByText("backend unreachable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
