// ECharts 折线图：极简包装，按需要传 series
import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
]);

interface SeriesItem {
  name: string;
  data: number[];
  color?: string;
}

interface LineTrendProps {
  xAxis: string[];
  series: SeriesItem[];
  height?: number;
}

export function LineTrend({ xAxis, series, height = 240 }: LineTrendProps) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current);
    const onResize = () => inst.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst.current?.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    if (!inst.current) return;
    inst.current.setOption({
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: "#888" } },
      grid: { left: 30, right: 16, top: 36, bottom: 24 },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: xAxis,
        axisLine: { lineStyle: { color: "#888" } },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { type: "dashed", color: "#e5e7eb" } },
      },
      series: series.map((s) => ({
        name: s.name,
        type: "line",
        smooth: true,
        showSymbol: false,
        data: s.data,
        lineStyle: s.color ? { color: s.color } : undefined,
        itemStyle: s.color ? { color: s.color } : undefined,
      })),
    });
  }, [xAxis, series]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
