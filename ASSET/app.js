(() => {
  const TWO_PI = Math.PI * 2;
  const DEG = Math.PI / 180;

  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  const norm720 = (angle) => {
    let a = angle % 720;
    if (a < 0) a += 720;
    return a;
  };

  class Cylinder {
    constructor(index, crankOffset) {
      this.index = index;
      this.crankOffset = crankOffset;
      this.pistonPosition = 0;
      this.intakeValveLift = 0;
      this.exhaustValveLift = 0;
      this.sparkActive = false;
      this.pressureState = 0.2;
      this.stroke = 'Intake';
      this.flowIn = false;
      this.flowOut = false;
      this.phaseAngle = 0;
    }
  }

  class Engine {
    constructor() {
      this.cylinderCount = 4;
      this.cycle = 720;
      this.firingOrder = [1, 3, 4, 2];

      this.crankRadius = 32;
      this.rodLength = 118;
      this.stroke = this.crankRadius * 2;
      this.pistonMin = this.rodLength - this.crankRadius;
      this.pistonMax = this.rodLength + this.crankRadius;

      this.crankAngle = 0;
      this.rpm = 1200;
      this.intakeCamPhase = 0;
      this.exhaustCamPhase = 0;
      this.sparkAdvance = 10;
      this.ignitionDelay = 7;

      this.intakeOpen = 350;
      this.intakeClose = 580;
      this.exhaustOpen = 140;
      this.exhaustClose = 370;

      this.maxValveLift = 1;
      this.intakeManifoldPressure = 0.25;
      this.exhaustPressure = 0.2;

      this.playing = true;
      this.phaseMap = this.buildFiringPhaseMap();
      this.cylinders = Array.from({ length: this.cylinderCount }, (_, i) => {
        const crankOffset = (720 / this.cylinderCount) * i;
        return new Cylinder(i + 1, crankOffset);
      });

      this.update(0);
    }

    buildFiringPhaseMap() {
      const map = new Map();
      const spacing = this.cycle / this.firingOrder.length;
      this.firingOrder.forEach((cyl, i) => map.set(cyl, i * spacing));
      return map;
    }

    camAngle(crankAngle) {
      return norm720(crankAngle / 2);
    }

    sliderCrankPosition(thetaDeg) {
      const t = thetaDeg * DEG;
      const sin = Math.sin(t);
      const cos = Math.cos(t);
      const r = this.crankRadius;
      const l = this.rodLength;
      return r * cos + Math.sqrt(Math.max(0, l * l - (r * sin) * (r * sin)));
    }

    normalizePistonTravel(pos) {
      return 1 - (pos - this.pistonMin) / (this.pistonMax - this.pistonMin);
    }

    inWindow(angle, open, close) {
      const a = norm720(angle);
      const o = norm720(open);
      const c = norm720(close);
      if (o <= c) return a >= o && a <= c;
      return a >= o || a <= c;
    }

    lobeLift(crankAngle, open, close, maxLift) {
      if (!this.inWindow(crankAngle, open, close)) return 0;
      const dur = (close - open + 720) % 720;
      const progress = ((norm720(crankAngle) - norm720(open) + 720) % 720) / dur;
      const fastOpen = Math.pow(Math.sin(Math.PI * progress), 0.78);
      const slowCloseBias = 0.86 + 0.14 * Math.cos(Math.PI * progress);
      return maxLift * fastOpen * slowCloseBias;
    }

    localCycleAngle(cyl) {
      const phase = this.phaseMap.get(cyl.index) || 0;
      return norm720(this.crankAngle - phase);
    }

    strokeFromAngle(a) {
      if (a >= 0 && a < 180) return 'Intake';
      if (a >= 180 && a < 360) return 'Compression';
      if (a >= 360 && a < 540) return 'Power';
      return 'Exhaust';
    }

    isTDCCompression(cyl) {
      const a = this.localCycleAngle(cyl);
      return Math.abs(a - 360) <= 3;
    }

    isTDCOverlap(cyl) {
      const a = this.localCycleAngle(cyl);
      return a <= 3 || a >= 717;
    }

    pressureFromState(stroke, a, sparkActive) {
      if (stroke === 'Intake') return 0.22 + 0.04 * Math.sin(a * DEG);
      if (stroke === 'Compression') return 0.3 + (a - 180) / 180 * 0.6;
      if (stroke === 'Power') {
        const burnCenter = 372 + this.ignitionDelay;
        const dist = Math.abs(a - burnCenter);
        const spike = Math.max(0, 1 - dist / 60);
        return 0.6 + 0.85 * spike + (sparkActive ? 0.14 : 0);
      }
      return 0.4 - (a - 540) / 180 * 0.24;
    }

    sparkForCylinder(cyl, localAngle) {
      const sparkAt = 360 - this.sparkAdvance;
      return Math.abs(localAngle - sparkAt) <= 2;
    }

    update(dt) {
      if (this.playing && this.rpm > 0 && dt > 0) {
        const degPerSec = (this.rpm * 360) / 60;
        this.crankAngle = norm720(this.crankAngle + degPerSec * dt);
      }

      this.cylinders.forEach((cyl) => {
        const localAngle = this.localCycleAngle(cyl);
        cyl.phaseAngle = localAngle;
        const sliderPos = this.sliderCrankPosition(localAngle);
        cyl.pistonPosition = this.normalizePistonTravel(sliderPos);

        const intakeOpen = this.intakeOpen + this.intakeCamPhase;
        const intakeClose = this.intakeClose + this.intakeCamPhase;
        const exhaustOpen = this.exhaustOpen + this.exhaustCamPhase;
        const exhaustClose = this.exhaustClose + this.exhaustCamPhase;

        cyl.intakeValveLift = this.lobeLift(localAngle, intakeOpen, intakeClose, this.maxValveLift);
        cyl.exhaustValveLift = this.lobeLift(localAngle, exhaustOpen, exhaustClose, this.maxValveLift);

        cyl.stroke = this.strokeFromAngle(localAngle);
        cyl.sparkActive = cyl.stroke === 'Compression' && this.sparkForCylinder(cyl, localAngle);

        cyl.pressureState = clamp(this.pressureFromState(cyl.stroke, localAngle, cyl.sparkActive), 0.1, 1.6);
        cyl.flowIn = cyl.intakeValveLift > 0.06 && cyl.pressureState < this.intakeManifoldPressure + 0.25;
        cyl.flowOut = cyl.exhaustValveLift > 0.06 && cyl.pressureState > this.exhaustPressure;
      });
    }

    overlapDuration() {
      const open = this.intakeOpen + this.intakeCamPhase;
      const close = this.exhaustClose + this.exhaustCamPhase;
      let span = (close - open + 720) % 720;
      if (span > 180) span = 0;
      return span;
    }
  }

  class EngineRenderer {
    constructor(canvas, engine) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.engine = engine;
      this.options = { showLabels: true, showComponents: true, showFlow: true };
    }

    colorByStroke(stroke) {
      if (stroke === 'Intake') return '#2d7bd1';
      if (stroke === 'Compression') return '#98a7b3';
      if (stroke === 'Power') return '#d96525';
      return '#7f1f1f';
    }

    render() {
      const { ctx, canvas, engine } = this;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#0d1721';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      this.drawTimingBar();
      engine.cylinders.forEach((cyl, i) => this.drawCylinder(cyl, 90 + i * 255));
      if (this.options.showComponents) {
        this.drawCrankshaftBank();
        this.drawCamshafts();
      }
    }

    drawTimingBar() {
      const { ctx, engine } = this;
      ctx.fillStyle = '#193044';
      ctx.fillRect(40, 24, 1020, 26);
      const x = 40 + (engine.crankAngle / 720) * 1020;
      ctx.fillStyle = '#55d4ff';
      ctx.fillRect(40, 24, (engine.crankAngle / 720) * 1020, 26);
      ctx.strokeStyle = '#9bc3df';
      ctx.strokeRect(40, 24, 1020, 26);
      ctx.fillStyle = '#dbebf8';
      ctx.font = '13px ui-monospace';
      ctx.fillText(`Crank ${engine.crankAngle.toFixed(1)}° / Cam ${(engine.camAngle(engine.crankAngle)).toFixed(1)}°`, 48, 42);
      ctx.strokeStyle = '#ffd369';
      ctx.beginPath();
      ctx.moveTo(x, 22);
      ctx.lineTo(x, 54);
      ctx.stroke();
      ctx.fillStyle = '#ffd369';
      ctx.fillText('TDC marker reference visible by cylinder phase map', 740, 42);
    }

    drawCylinder(cyl, x) {
      const { ctx, engine } = this;
      const yTop = 120;
      const width = 190;
      const height = 345;

      ctx.strokeStyle = '#6f8aa0';
      ctx.lineWidth = 2;
      ctx.strokeRect(x, yTop, width, height);

      const intakeLiftPx = cyl.intakeValveLift * 40;
      const exhaustLiftPx = cyl.exhaustValveLift * 40;
      ctx.fillStyle = '#2d7bd1';
      ctx.fillRect(x + 28, yTop - 5 + intakeLiftPx, 12, 50 - intakeLiftPx);
      ctx.fillStyle = '#7f1f1f';
      ctx.fillRect(x + width - 40, yTop - 5 + exhaustLiftPx, 12, 50 - exhaustLiftPx);

      if (cyl.sparkActive) {
        ctx.fillStyle = '#ffe07c';
        ctx.beginPath();
        ctx.arc(x + width / 2, yTop + 18, 7, 0, TWO_PI);
        ctx.fill();
      }

      const strokeColor = this.colorByStroke(cyl.stroke);
      const pressureHeight = Math.max(8, (cyl.pressureState / 1.6) * (height - 56));
      ctx.fillStyle = strokeColor;
      ctx.globalAlpha = 0.2 + (cyl.pressureState / 1.6) * 0.4;
      ctx.fillRect(x + 10, yTop + height - 10 - pressureHeight, width - 20, pressureHeight);
      ctx.globalAlpha = 1;

      const pistonY = yTop + 58 + cyl.pistonPosition * (height - 110);
      ctx.fillStyle = '#9fb4c6';
      ctx.fillRect(x + 22, pistonY, width - 44, 30);

      const crankCx = x + width / 2;
      const crankCy = yTop + height + 72;
      const localTheta = cyl.phaseAngle * DEG;
      const pinX = crankCx + engine.crankRadius * Math.sin(localTheta);
      const pinY = crankCy + engine.crankRadius * Math.cos(localTheta);
      const wristX = x + width / 2;
      const wristY = pistonY + 15;
      ctx.strokeStyle = '#8fa3b4';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(wristX, wristY);
      ctx.lineTo(pinX, pinY);
      ctx.stroke();

      if (this.options.showFlow) {
        if (cyl.flowIn) {
          ctx.strokeStyle = '#46a0ff';
          ctx.beginPath();
          ctx.moveTo(x - 28, yTop + 20);
          ctx.lineTo(x + 28, yTop + 20);
          ctx.stroke();
        }
        if (cyl.flowOut) {
          ctx.strokeStyle = '#b14a4a';
          ctx.beginPath();
          ctx.moveTo(x + width - 28, yTop + 20);
          ctx.lineTo(x + width + 30, yTop + 20);
          ctx.stroke();
        }
      }

      if (this.options.showLabels) {
        ctx.fillStyle = '#d9e7f2';
        ctx.font = '12px ui-monospace';
        ctx.fillText(`Cylinder ${cyl.index}`, x + 8, yTop - 16);
        ctx.fillText(`${cyl.stroke}  P=${cyl.pressureState.toFixed(2)}`, x + 8, yTop + height + 20);
        ctx.fillText(`TDCc ${engine.isTDCCompression(cyl) ? 'yes' : 'no'}  TDCov ${engine.isTDCOverlap(cyl) ? 'yes' : 'no'}`, x + 8, yTop + height + 38);
      }
    }

    drawCrankshaftBank() {
      const { ctx } = this;
      ctx.fillStyle = '#223a4f';
      ctx.fillRect(50, 555, 1030, 16);
    }

    drawCamshafts() {
      const { ctx, engine } = this;
      const baseY = 90;
      const lobeR = 12;
      for (let i = 0; i < engine.cylinders.length; i += 1) {
        const x = 184 + i * 255;
        const camThetaIn = (engine.camAngle(engine.cylinders[i].phaseAngle + engine.intakeCamPhase)) * DEG;
        const camThetaEx = (engine.camAngle(engine.cylinders[i].phaseAngle + engine.exhaustCamPhase)) * DEG;

        ctx.fillStyle = '#2d7bd1';
        ctx.beginPath();
        ctx.ellipse(x - 58, baseY, lobeR + 5 * Math.sin(camThetaIn), lobeR, camThetaIn, 0, TWO_PI);
        ctx.fill();

        ctx.fillStyle = '#7f1f1f';
        ctx.beginPath();
        ctx.ellipse(x + 58, baseY, lobeR + 5 * Math.sin(camThetaEx), lobeR, camThetaEx, 0, TWO_PI);
        ctx.fill();

        ctx.strokeStyle = '#9bb2c6';
        ctx.beginPath();
        ctx.moveTo(x - 58, baseY + 10);
        ctx.lineTo(x - 58, 118 + engine.cylinders[i].intakeValveLift * 25);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(x + 58, baseY + 10);
        ctx.lineTo(x + 58, 118 + engine.cylinders[i].exhaustValveLift * 25);
        ctx.stroke();
      }
    }
  }

  const engine = new Engine();
  const canvas = document.getElementById('engineCanvas');
  const renderer = new EngineRenderer(canvas, engine);

  const el = {
    rpm: document.getElementById('rpm'),
    rpmValue: document.getElementById('rpmValue'),
    angle: document.getElementById('angle'),
    angleValue: document.getElementById('angleValue'),
    sparkAdvance: document.getElementById('sparkAdvance'),
    sparkAdvanceValue: document.getElementById('sparkAdvanceValue'),
    intakePhase: document.getElementById('intakePhase'),
    intakePhaseValue: document.getElementById('intakePhaseValue'),
    exhaustPhase: document.getElementById('exhaustPhase'),
    exhaustPhaseValue: document.getElementById('exhaustPhaseValue'),
    playPause: document.getElementById('playPause'),
    showLabels: document.getElementById('showLabels'),
    showComponents: document.getElementById('showComponents'),
    showFlow: document.getElementById('showFlow'),
    globalReadout: document.getElementById('globalReadout'),
    cylReadout: document.getElementById('cylReadout')
  };

  function syncLabels() {
    el.rpmValue.textContent = `${engine.rpm.toFixed(0)}`;
    el.angleValue.textContent = `${engine.crankAngle.toFixed(1)}°`;
    el.sparkAdvanceValue.textContent = `${engine.sparkAdvance.toFixed(0)}°`;
    el.intakePhaseValue.textContent = `${engine.intakeCamPhase.toFixed(0)}°`;
    el.exhaustPhaseValue.textContent = `${engine.exhaustCamPhase.toFixed(0)}°`;

    const io = engine.intakeOpen + engine.intakeCamPhase;
    const ec = engine.exhaustClose + engine.exhaustCamPhase;
    el.globalReadout.innerHTML =
      `Intake opens at ${norm720(io).toFixed(1)}° crank | ` +
      `Exhaust closes at ${norm720(ec).toFixed(1)}° crank | ` +
      `Valve overlap ${engine.overlapDuration().toFixed(1)}° | ` +
      `Cam ratio 2:1 enforced`;

    el.cylReadout.innerHTML = engine.cylinders.map((c) => (
      `<div class="cell">` +
      `Cyl ${c.index} phase ${c.phaseAngle.toFixed(1)}°<br>` +
      `Stroke ${c.stroke}<br>` +
      `Piston ${(c.pistonPosition * 100).toFixed(1)}% down-bore<br>` +
      `Int lift ${(c.intakeValveLift * 100).toFixed(0)}% Exh lift ${(c.exhaustValveLift * 100).toFixed(0)}%<br>` +
      `Spark ${c.sparkActive ? 'active' : 'off'} Flow in ${c.flowIn ? 'yes' : 'no'} out ${c.flowOut ? 'yes' : 'no'}` +
      `</div>`
    )).join('');
  }

  el.rpm.addEventListener('input', (e) => {
    engine.rpm = Number(e.target.value);
    syncLabels();
  });

  el.angle.addEventListener('input', (e) => {
    engine.crankAngle = Number(e.target.value);
    engine.playing = false;
    el.playPause.textContent = 'Play';
    engine.update(0);
    syncLabels();
  });

  el.sparkAdvance.addEventListener('input', (e) => {
    engine.sparkAdvance = Number(e.target.value);
    engine.update(0);
    syncLabels();
  });

  el.intakePhase.addEventListener('input', (e) => {
    engine.intakeCamPhase = Number(e.target.value);
    engine.update(0);
    syncLabels();
  });

  el.exhaustPhase.addEventListener('input', (e) => {
    engine.exhaustCamPhase = Number(e.target.value);
    engine.update(0);
    syncLabels();
  });

  el.playPause.addEventListener('click', () => {
    engine.playing = !engine.playing;
    el.playPause.textContent = engine.playing ? 'Pause' : 'Play';
  });

  document.querySelectorAll('[data-step]').forEach((btn) => {
    btn.addEventListener('click', () => {
      engine.playing = false;
      el.playPause.textContent = 'Play';
      engine.crankAngle = norm720(engine.crankAngle + Number(btn.dataset.step));
      el.angle.value = engine.crankAngle.toFixed(1);
      engine.update(0);
      syncLabels();
    });
  });

  el.showLabels.addEventListener('change', () => { renderer.options.showLabels = el.showLabels.checked; });
  el.showComponents.addEventListener('change', () => { renderer.options.showComponents = el.showComponents.checked; });
  el.showFlow.addEventListener('change', () => { renderer.options.showFlow = el.showFlow.checked; });

  let last = performance.now();
  function loop(now) {
    const dt = (now - last) / 1000;
    last = now;

    engine.update(dt);
    el.angle.value = engine.crankAngle.toFixed(1);
    syncLabels();
    renderer.render();

    requestAnimationFrame(loop);
  }

  syncLabels();
  requestAnimationFrame(loop);
})();
