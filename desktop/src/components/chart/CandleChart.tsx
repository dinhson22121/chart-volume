import { useEffect, useRef, useState } from "react";
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
import { api } from "../../api/client";
import type { Analysis, Candle, IndicatorSeries } from "../../types";
import { signalIsBullish, signalIsEntry } from "../../lib/wyckoff";
import { formatPrice, priceMinMove } from "../../lib/price";
import { useI18n } from "../../i18n/I18nContext";
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
  dragon: "#9575cd",
  t3Fast: "#4fc3f7",
  t3Slow: "#ffb74d",
  entry: "#4fc3f7",
  poc: "#ce93d8",
  valueArea: "#78909c",
};

function toTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

interface Props {
  candles: Candle[];
  analysis: Analysis | null;
  onBarClick?: (bucketStartIso: string) => void;
}

export function CandleChart({ candles, analysis, onBarClick }: Props) {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const dragonSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const t3FastSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const t3SlowSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const [indicators, setIndicators] = useState<IndicatorSeries | null>(null);
  // Click handling is wired once at chart creation; keep the latest candles
  // and callback in refs so that closure isn't stale across re-renders.
  const candlesRef = useRef<Candle[]>(candles);
  const onBarClickRef = useRef(onBarClick);
  candlesRef.current = candles;
  onBarClickRef.current = onBarClick;

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

    // Sonic R overlay lines -- empty until analysis.strategy === "sonicr" data
    // arrives (see the fetch effect below), harmless no-op otherwise.
    const dragonSeries = chart.addLineSeries({
      color: COLORS.dragon,
      lineWidth: 2,
      title: "Dragon",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const t3FastSeries = chart.addLineSeries({
      color: COLORS.t3Fast,
      lineWidth: 1,
      title: "T3 fast",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const t3SlowSeries = chart.addLineSeries({
      color: COLORS.t3Slow,
      lineWidth: 1,
      title: "T3 slow",
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    dragonSeriesRef.current = dragonSeries;
    t3FastSeriesRef.current = t3FastSeries;
    t3SlowSeriesRef.current = t3SlowSeries;

    const handleClick = (param: { time?: Time }) => {
      if (!param.time || !onBarClickRef.current) return;
      const match = candlesRef.current.find((c) => toTime(c.bucket_start) === param.time);
      if (match) onBarClickRef.current(match.bucket_start);
    };
    chart.subscribeClick(handleClick);

    return () => {
      chart.unsubscribeClick(handleClick);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      dragonSeriesRef.current = null;
      t3FastSeriesRef.current = null;
      t3SlowSeriesRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  // Fetch Sonic R's Dragon/T3 series whenever the active analysis is Sonic R.
  // Self-contained (like TracePanel) rather than threaded through App.tsx,
  // since it's purely a chart-presentation concern keyed off ticker/timeframe.
  useEffect(() => {
    if (analysis?.strategy !== "sonicr") {
      setIndicators(null);
      return;
    }
    let cancelled = false;
    api
      .getIndicators(analysis.ticker, analysis.timeframe)
      .then((data) => {
        if (!cancelled) setIndicators(data);
      })
      .catch(() => {
        if (!cancelled) setIndicators(null);
      });
    return () => {
      cancelled = true;
    };
  }, [analysis?.strategy, analysis?.ticker, analysis?.timeframe]);

  // Update data, markers and support/resistance lines.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    if (!candleSeries || !volumeSeries) return;

    // Fixed 2-decimal precision rounds sub-cent crypto prices (e.g.
    // 0.00000123) down to "0.00" on the axis/price lines -- a custom
    // formatter keyed off the latest close's own magnitude fixes that, and
    // switches to compact "0.0<n>xxx" notation for extreme micro-cap prices
    // the same way formatPrice() does for the analysis panel.
    if (candles.length > 0) {
      const sample = candles[candles.length - 1].close;
      candleSeries.applyOptions({
        priceFormat: { type: "custom", formatter: formatPrice, minMove: priceMinMove(sample) },
      });
    }

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

    // Markers for detected Wyckoff events. LPS/LPSY are confirmed entry points
    // (a pullback re-testing a broken level) -- rendered as a filled circle so
    // they stand out from the arrow markers of the other 8 raw detectors.
    const markers: SeriesMarker<Time>[] = (analysis?.signals ?? [])
      .filter((s) => s.ts)
      .map((s) => {
        const bull = signalIsBullish(s.type);
        const entry = signalIsEntry(s.type);
        // Volume Profile confirmation (Wyckoff only, see chart.poc/VAH/VAL
        // lines above) -- append a checkmark so a confirmed breakout/reversal
        // stands out from an unconfirmed one of the same type.
        const vpSuffix = s.volume_confirmed ? " ✓" : "";
        return {
          time: toTime(s.ts as string),
          position: bull ? "belowBar" : "aboveBar",
          color: bull ? COLORS.bull : COLORS.bear,
          shape: entry ? "circle" : bull ? "arrowUp" : "arrowDown",
          text: (entry ? `${s.type} ●` : s.type) + vpSuffix,
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
          title: t("chart.support"),
        }),
        candleSeries.createPriceLine({
          price: analysis.levels.resistance,
          color: COLORS.resistance,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: t("chart.resistance"),
        }),
      );

      // Volume Profile (Wyckoff only) -- POC + Value Area, when computed.
      const { poc, value_area_high, value_area_low } = analysis.levels;
      if (poc != null && value_area_high != null && value_area_low != null) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: poc,
            color: COLORS.poc,
            lineStyle: LineStyle.Solid,
            lineWidth: 1,
            axisLabelVisible: true,
            title: t("chart.poc"),
          }),
          candleSeries.createPriceLine({
            price: value_area_high,
            color: COLORS.valueArea,
            lineStyle: LineStyle.Dotted,
            lineWidth: 1,
            axisLabelVisible: true,
            title: t("chart.valueAreaHigh"),
          }),
          candleSeries.createPriceLine({
            price: value_area_low,
            color: COLORS.valueArea,
            lineStyle: LineStyle.Dotted,
            lineWidth: 1,
            axisLabelVisible: true,
            title: t("chart.valueAreaLow"),
          }),
        );
      }
    }

    // Entry / SL / TP lines for the active (or last) trade scenario.
    if (analysis?.scenario) {
      const { entry, stop_loss, take_profit } = analysis.scenario;
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: entry,
          color: COLORS.entry,
          lineStyle: LineStyle.Solid,
          lineWidth: 2,
          axisLabelVisible: true,
          title: t("chart.scenarioEntry"),
        }),
        candleSeries.createPriceLine({
          price: stop_loss,
          color: COLORS.bear,
          lineStyle: LineStyle.Solid,
          lineWidth: 2,
          axisLabelVisible: true,
          title: t("chart.scenarioSl"),
        }),
        candleSeries.createPriceLine({
          price: take_profit,
          color: COLORS.bull,
          lineStyle: LineStyle.Solid,
          lineWidth: 2,
          axisLabelVisible: true,
          title: t("chart.scenarioTp"),
        }),
      );
    }

    chartRef.current?.timeScale().fitContent();
  }, [candles, analysis, t]);

  // Sonic R Dragon/T3 overlay lines -- separate effect since `indicators`
  // arrives asynchronously after `analysis`/`candles` are already rendered.
  useEffect(() => {
    const dragonSeries = dragonSeriesRef.current;
    const t3FastSeries = t3FastSeriesRef.current;
    const t3SlowSeries = t3SlowSeriesRef.current;
    if (!dragonSeries || !t3FastSeries || !t3SlowSeries) return;

    dragonSeries.setData(indicators ? indicators.dragon.map((p) => ({ time: toTime(p.ts), value: p.value })) : []);
    t3FastSeries.setData(indicators ? indicators.t3_fast.map((p) => ({ time: toTime(p.ts), value: p.value })) : []);
    t3SlowSeries.setData(indicators ? indicators.t3_slow.map((p) => ({ time: toTime(p.ts), value: p.value })) : []);
  }, [indicators]);

  return (
    <div className="chart-wrap">
      <div className="chart" ref={containerRef} />
      {onBarClick && <span className="chart-hint faint">{t("chart.hint")}</span>}
    </div>
  );
}
