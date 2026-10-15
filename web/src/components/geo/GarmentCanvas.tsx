"use client";

import { useEffect, useRef } from "react";

function bodyPath(ctx: CanvasRenderingContext2D) {
  ctx.beginPath();
  ctx.moveTo(-20, -35);
  ctx.quadraticCurveTo(0, -25, 20, -35);
  ctx.lineTo(45, -28);
  ctx.lineTo(55, -5);
  ctx.lineTo(38, 5);
  ctx.lineTo(32, 38);
  ctx.lineTo(-32, 38);
  ctx.lineTo(-38, 5);
  ctx.lineTo(-55, -5);
  ctx.lineTo(-45, -28);
  ctx.closePath();
}

export function GarmentCanvas({ color }: { color: string }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const colorRef = useRef(color);
  colorRef.current = color;

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    let frame = 0;
    let angle = 0;
    let dragging = false;
    let lastX = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    const draw = () => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      if (!dragging) angle += 0.01;

      ctx.save();
      ctx.translate(w / 2, h / 2 + Math.sin(angle * 2) * 2);
      const s = Math.min(w, h) / 125;
      ctx.scale(s * (0.72 + 0.28 * Math.abs(Math.cos(angle))), s);

      ctx.shadowColor = "rgba(0, 243, 255, 0.55)";
      ctx.shadowBlur = 12;
      ctx.fillStyle = colorRef.current;
      ctx.strokeStyle = "#00f3ff";
      ctx.lineWidth = 1.5;
      bodyPath(ctx);
      ctx.fill();
      ctx.stroke();

      ctx.shadowBlur = 0;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.2)";
      ctx.beginPath();
      ctx.moveTo(-15, -10);
      ctx.quadraticCurveTo(0, 10, 15, -10);
      ctx.stroke();

      const shade = ctx.createLinearGradient(-55, 0, 55, 0);
      const k = Math.cos(angle);
      shade.addColorStop(0, `rgba(0, 0, 0, ${0.25 + 0.2 * k})`);
      shade.addColorStop(0.5, "rgba(255, 255, 255, 0.05)");
      shade.addColorStop(1, `rgba(0, 0, 0, ${0.25 - 0.2 * k})`);
      ctx.fillStyle = shade;
      bodyPath(ctx);
      ctx.fill();
      ctx.restore();

      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);

    const down = (e: PointerEvent) => {
      dragging = true;
      lastX = e.clientX;
    };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      angle += (e.clientX - lastX) * 0.02;
      lastX = e.clientX;
    };
    const up = () => {
      dragging = false;
    };
    canvas.addEventListener("pointerdown", down);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(frame);
      canvas.removeEventListener("pointerdown", down);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={ref} style={{ cursor: "grab" }} />;
}
