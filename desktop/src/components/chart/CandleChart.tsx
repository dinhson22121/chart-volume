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
import type { Analysis, Candle, DrawingShape, IndicatorSeries } from "../../types";
import { signalIsBullish, signalIsEntry, zoneLabel } from "../../lib/wyckoff";
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
  premium: "#e0574b",
  discount: "#f0c419",
};

// Translucent fills for zone bands (addZoneBand) -- separate from the
// line/marker COLORS above since a solid color would be far too opaque as a
// full-width fill.
const ZONE_FILL = {
  support: "rgba(46, 189, 133, 0.22)",
  resistance: "rgba(224, 165, 78, 0.22)",
  premium: "rgba(224, 87, 75, 0.18)",
  discount: "rgba(240, 196, 25, 0.18)",
  valueArea: "rgba(120, 144, 156, 0.25)",
  bullOb: "rgba(79, 195, 247, 0.22)",
  bearOb: "rgba(224, 87, 75, 0.22)",
};

function toTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

// Support/resistance-style levels aren't exact prices in practice -- a band
// around the value reads as "an area of confluence" rather than a single
// hairline. +/-0.3% is a small, fixed visual width (not a detection
// parameter -- purely cosmetic).
const LEVEL_BAND_PCT = 0.003;

// Renders a price range as a filled horizontal band spanning the chart's
// full time axis, via a Baseline series (fills between `low`, the
// baseValue, and `high`, the series' own data value) -- lightweight-charts
// v4 has no simpler "shaded rectangle" primitive. The line itself is
// colored to match the fill so only the shaded area reads, not a hairline.
function addZoneBand(chart: IChartApi, candles: Candle[], low: number, high: number, fillColor: string) {
  const series = chart.addBaselineSeries({
    baseValue: { type: "price", price: low },
    topFillColor1: fillColor,
    topFillColor2: fillColor,
    bottomFillColor1: "transparent",
    bottomFillColor2: "transparent",
    topLineColor: fillColor,
    bottomLineColor: "transparent",
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
  series.setData(candles.map((c) => ({ time: toTime(c.bucket_start), value: high })));
  return series;
}

interface Props {
  candles: Candle[];
  analysis: Analysis | null;
  onBarClick?: (bucketStartIso: string) => void;
}

export function CandleChart({ candles, analysis, onBarClick }: Props) {
  const { t, language } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const dragonSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const t3FastSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const t3SlowSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const zoneSeriesRef = useRef<ISeriesApi<"Baseline">[]>([]);
  const drawingSeriesRef = useRef<ISeriesApi<"Line">[]>([]);
  const [indicators, setIndicators] = useState<IndicatorSeries | null>(null);
  const [isDrawingMode, setIsDrawingMode] = useState(false);
  const [shapes, setShapes] = useState<DrawingShape[]>([]);
  // Click handling is wired once at chart creation; keep the latest candles
  // and callback in refs so that closure isn't stale across re-renders.
  const candlesRef = useRef<Candle[]>(candles);
  const onBarClickRef = useRef(onBarClick);
  const isDrawingModeRef = useRef(isDrawingMode);
  const pendingPointRef = useRef<{ time: string; price: number } | null>(null);
  candlesRef.current = candles;
  onBarClickRef.current = onBarClick;
  isDrawingModeRef.current = isDrawingMode;

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

    const handleClick = (param: { time?: Time; point?: { x: number; y: number } }) => {
      if (!param.time) return;
      const match = candlesRef.current.find((c) => toTime(c.bucket_start) === param.time);
      if (!match) return;

      if (isDrawingModeRef.current) {
        if (!param.point) return;
        const price = candleSeries.coordinateToPrice(param.point.y);
        if (price == null) return;
        if (!pendingPointRef.current) {
          pendingPointRef.current = { time: match.bucket_start, price };
        } else {
          const first = pendingPointRef.current;
          pendingPointRef.current = null;
          setShapes((prev) => [
            ...prev,
            { points: [first, { time: match.bucket_start, price }], color: "#4fc3f7" },
          ]);
        }
        return;
      }

      onBarClickRef.current?.(match.bucket_start);
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
      zoneSeriesRef.current = [];
      drawingSeriesRef.current = [];
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

  // Load this ticker/timeframe's saved trend lines. skipSaveRef guards the
  // save effect below from firing on this load itself -- otherwise it would
  // immediately PUT back the exact shapes it just GOT, on every ticker switch.
  const skipSaveRef = useRef(false);
  useEffect(() => {
    if (!analysis) {
      setShapes([]);
      return;
    }
    let cancelled = false;
    api
      .getDrawings(analysis.ticker, analysis.timeframe)
      .then((data) => {
        if (cancelled) return;
        skipSaveRef.current = true;
        setShapes(data.shapes);
      })
      .catch(() => {
        if (!cancelled) setShapes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [analysis?.ticker, analysis?.timeframe]);

  // Persist trend lines whenever they change (new line drawn, or "clear"),
  // except the load above setting them fresh from the server.
  useEffect(() => {
    if (skipSaveRef.current) {
      skipSaveRef.current = false;
      return;
    }
    if (!analysis) return;
    api.saveDrawings(analysis.ticker, analysis.timeframe, shapes).catch(() => {
      // best-effort -- a failed save just means this line isn't persisted;
      // it's still visible for the rest of the session.
    });
  }, [shapes]);

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

    // Clear previous price lines / zone bands.
    priceLinesRef.current.forEach((line) => candleSeries.removePriceLine(line));
    priceLinesRef.current = [];
    if (chartRef.current) {
      zoneSeriesRef.current.forEach((series) => chartRef.current!.removeSeries(series));
    }
    zoneSeriesRef.current = [];

    // Markers for actionable events only (bullish, non-continuation -- the
    // same qualifying filter trade_scenario._create_scenarios uses, see
    // app.api.analysis._actionable_signals). The full analysis.signals list
    // includes bearish and trend-confirmation events too (NoDemand/NoSupply,
    // etc.) -- rendering every one of those as its own always-visible text
    // label made the chart's marker text collide and pile up on tickers with
    // frequent signals, for events that were never tradeable anyway (spot
    // trading has no short-selling). LPS/LPSY are confirmed entry points (a
    // pullback re-testing a broken level) -- rendered as a filled circle so
    // they stand out from the arrow markers of the other detectors.
    const markers: SeriesMarker<Time>[] = (analysis?.actionable_signals ?? [])
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

    // Support / resistance lines. When SMC's Premium/Discount/Equilibrium
    // zones are available (see app.smc.zones -- null for every other
    // strategy), support/resistance ARE the current swing low/high, so their
    // Strong/Weak label is appended straight onto these same two titles
    // rather than drawn as separate lines (avoids the marker-clutter problem
    // fixed earlier this session).
    if (analysis && analysis.phase !== "Insufficient data" && chartRef.current) {
      const chart = chartRef.current;
      const zones = analysis.levels.smc_zones;
      const supportTitle = zones ? `${t("chart.support")} (${zoneLabel(zones.low_label, language)})` : t("chart.support");
      const resistanceTitle = zones
        ? `${t("chart.resistance")} (${zoneLabel(zones.high_label, language)})`
        : t("chart.resistance");
      priceLinesRef.current.push(
        candleSeries.createPriceLine({
          price: analysis.levels.support,
          color: COLORS.support,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: supportTitle,
        }),
        candleSeries.createPriceLine({
          price: analysis.levels.resistance,
          color: COLORS.resistance,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: resistanceTitle,
        }),
      );
      zoneSeriesRef.current.push(
        addZoneBand(
          chart, candles,
          analysis.levels.support * (1 - LEVEL_BAND_PCT), analysis.levels.support * (1 + LEVEL_BAND_PCT),
          ZONE_FILL.support,
        ),
        addZoneBand(
          chart, candles,
          analysis.levels.resistance * (1 - LEVEL_BAND_PCT), analysis.levels.resistance * (1 + LEVEL_BAND_PCT),
          ZONE_FILL.resistance,
        ),
      );

      // Premium/Discount zone boundaries -- display-only reference (never
      // gates a trade, see app.smc.zones' own docstring): the top-5%/
      // bottom-5% boundary lines, plus a filled band from that boundary to
      // the resistance/support edge (the zone's own natural width), not the
      // equilibrium band too, to keep this to 2 zones instead of 3.
      if (zones) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: zones.premium_low,
            color: COLORS.premium,
            lineStyle: LineStyle.Dotted,
            lineWidth: 1,
            axisLabelVisible: true,
            title: t("chart.premiumZone"),
          }),
          candleSeries.createPriceLine({
            price: zones.discount_high,
            color: COLORS.discount,
            lineStyle: LineStyle.Dotted,
            lineWidth: 1,
            axisLabelVisible: true,
            title: t("chart.discountZone"),
          }),
        );
        zoneSeriesRef.current.push(
          addZoneBand(chart, candles, zones.premium_low, analysis.levels.resistance, ZONE_FILL.premium),
          addZoneBand(chart, candles, analysis.levels.support, zones.discount_high, ZONE_FILL.discount),
        );
      }

      // Volume Profile (Wyckoff only) -- POC + Value Area, when computed.
      const { poc, value_area_high, value_area_low } = analysis.levels;
      if (poc != null && value_area_high != null && value_area_low != null) {
        zoneSeriesRef.current.push(addZoneBand(chart, candles, value_area_low, value_area_high, ZONE_FILL.valueArea));
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

      // Order Blocks (SMC only) -- the anchor candle's own low/high, drawn
      // as a filled zone (see app.smc.events.SMCEvent.zone_low/zone_high).
      // Mitigated ones (price already closed back through the zone -- its
      // premise no longer holds) are skipped entirely rather than shown
      // faded, to avoid cluttering the chart with zones that no longer mean
      // anything.
      const OB_TYPES = new Set(["BullishOB", "BearishOB", "SwingBullishOB", "SwingBearishOB"]);
      for (const s of analysis.signals ?? []) {
        if (!OB_TYPES.has(s.type) || s.mitigated || s.zone_low == null || s.zone_high == null) continue;
        const bullish = s.type === "BullishOB" || s.type === "SwingBullishOB";
        zoneSeriesRef.current.push(
          addZoneBand(chart, candles, s.zone_low, s.zone_high, bullish ? ZONE_FILL.bullOb : ZONE_FILL.bearOb),
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

  // Render the user's own trend lines (see api.getDrawings/saveDrawings) --
  // one 2-point Line series per shape, same dynamic-array-of-series pattern
  // as the Order Block zone bands above.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    drawingSeriesRef.current.forEach((series) => chart.removeSeries(series));
    drawingSeriesRef.current = shapes.map((shape) => {
      const series = chart.addLineSeries({
        color: shape.color,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      series.setData(shape.points.map((p) => ({ time: toTime(p.time), value: p.price })));
      return series;
    });
  }, [shapes]);

  return (
    <div className="chart-wrap">
      <div className="chart" ref={containerRef} />
      {onBarClick && (
        <div className="chart-toolbar">
          <button
            type="button"
            className={isDrawingMode ? "btn chart-draw-btn active" : "btn chart-draw-btn"}
            onClick={() => {
              pendingPointRef.current = null;
              setIsDrawingMode((v) => !v);
            }}
          >
            {isDrawingMode ? t("chart.drawingActive") : t("chart.draw")}
          </button>
          {shapes.length > 0 && (
            <button type="button" className="btn chart-draw-btn" onClick={() => setShapes([])}>
              {t("chart.clearDrawings")}
            </button>
          )}
        </div>
      )}
      {onBarClick && (
        <span className="chart-hint faint">{isDrawingMode ? t("chart.drawHint") : t("chart.hint")}</span>
      )}
    </div>
  );
}
