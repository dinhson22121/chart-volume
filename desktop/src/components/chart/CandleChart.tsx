import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Analysis, Candle } from "../../types";
import { signalIsBullish } from "../../lib/wyckoff";
import "./chart.css";

const COLORS = {
  up: "#2ebd85",
  down: "#e0574b",
  volUp: "rgba(46, 189, 133, 0.4)",
  volDown: "rgba(224, 87, 75, 0.4)",
  support: "#2ebd85",
  resistance: "#e0a54e",
  bull: "#2ebd85",
  bear: "#e0574b",
  grid: "rgba(255,255,255,0.05)",
  text: "#a9b2c0",
};

function toTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

interface Props {
  candles: Candle[];
  analysis: Analysis | null;
}

export function CandleChart({ candles, analysis }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  // Create the chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: COLORS.text,
        fontFamily: "JetBrains Mono, monospace",
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)", timeVisible: true },
      autoSize: true,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  // Update data, markers and support/resistance lines.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    if (!candleSeries || !volumeSeries) return;

    candleSeries.setData(
      candles.map((c) => ({
        time: toTime(c.bucket_start),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );
    volumeSeries.setData(
      candles.map((c) => ({
        time: toTime(c.bucket_start),
        value: c.volume,
        color: c.close >= c.open ? COLORS.volUp : COLORS.volDown,
      })),
    );

    // Clear previous price lines.
    priceLinesRef.current.forEach((line) => candleSeries.removePriceLine(line));
    priceLinesRef.current = [];

    // Markers for detected Wyckoff events.
    const markers: SeriesMarker<Time>[] = (analysis?.signals ?? [])
      .filter((s) => s.ts)
      .map((s) => {
        const bull = signalIsBullish(s.type);
        return {
          time: toTime(s.ts as string),
          position: bull ? "belowBar" : "aboveBar",
          color: bull ? COLORS.bull : COLORS.bear,
          shape: bull ? "arrowUp" : "arrowDown",
          text: s.type,
        } as SeriesMarker<Time>;
      })
      .sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);

    // Support / resistance lines.
    if (analysis && analysis.phase !== "Insufficient data") {
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: analysis.levels.support,
          color: COLORS.support,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: "Hỗ trợ",
        }),
        candleSeries.createPriceLine({
          price: analysis.levels.resistance,
          color: COLORS.resistance,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: "Kháng cự",
        }),
      );
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, analysis]);

  return <div className="chart" ref={containerRef} />;
}
