"""Optional Chromium check of field, mode and height controls in the HTML.

Run from the repo root: python -m cfd.tests.browser_wind_viewer
Only generated checks/screenshots under build/previews/ are modified.
"""
import argparse
import html
import json
from pathlib import Path
import re
import shutil
import subprocess


CHECK = r"""
<script>
(async function(){
 const checks=[];
 const assert=(condition,message)=>{if(!condition)throw Error(message);checks.push(message);};
 const pause=()=>new Promise(resolve=>setTimeout(resolve,100));
 try{
  for(let i=0;i<150&&!window.viewerReady;i++)await pause();
  assert(window.viewerReady,'Viewer opens');
  assert(gd.data[volumeIndex].i.length>0,'Actual 3D contour triangles exist');
  assert(gd.data[volumeIndex].intensity.some(v=>v<0)&&gd.data[volumeIndex].intensity.some(v=>v>0),'Both red and blue values exist');
  await document.getElementById('sliceMode').onclick();
  assert(gd.data[exactIndex].visible&&!gd.data[volumeIndex].visible,'4 m uses exact saved 2D triangles');
  const select=document.getElementById('field');select.value='direction';await select.onchange({target:select});
  assert(window.viewerState.field==='direction','Direction field switches');
  assert(Math.abs(gd.data[exactIndex].intensity[100]-planeFields.direction[100])<1e-7,'Direction values switch with the label');
  const slider=document.getElementById('height');slider.value='6';await slider.onchange({target:slider});
  assert(gd.data[planeIndex].visible&&!gd.data[exactIndex].visible&&window.viewerState.height===6,'Height switches to a real 6 m grid cut');
  select.value='speed';await select.onchange({target:select});
  assert(window.viewerState.field==='speed','Speed field switches');
  await document.getElementById('bothMode').onclick();
  assert(gd.data[volumeIndex].visible&&gd.data[planeIndex].visible,'Both mode shows field and cut');
  const opacity=document.getElementById('opacity');opacity.value='.27';await opacity.onchange({target:opacity});
  assert(gd.data[volumeIndex].opacity===.27,'Opacity changes');
  await document.getElementById('fullView').onclick();
  assert(gd.layout.scene.xaxis.range[0]===info.bounds_m[0][0],'Whole-field camera works');
  await document.getElementById('nearView').onclick();
  await document.getElementById('topView').onclick();
  assert(gd.layout.scene.camera.up.x===1,'Top view puts the bow upward');
  select.value='goodness';await select.onchange({target:select});
  slider.value='4';await slider.onchange({target:slider});
  await document.getElementById('volumeMode').onclick();
  await document.getElementById('nearView').onclick();
  const pre=document.createElement('pre');pre.id='viewer-smoke-result';pre.style.display='none';pre.textContent=JSON.stringify({passed:true,checks});document.body.appendChild(pre);
 }catch(error){const pre=document.createElement('pre');pre.id='viewer-smoke-result';pre.textContent=JSON.stringify({passed:false,error:String(error),checks});document.body.appendChild(pre);}
})();
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer", type=Path, default=Path("cfd/results/v1_5ms/visualizations/wind_3d.html"))
    parser.add_argument("--output", type=Path, default=Path("build/previews/wind_3d"))
    args = parser.parse_args()
    browser = shutil.which("chromium-browser") or shutil.which("chromium")
    if not browser:
        parser.error("Chromium is required for this optional browser check")
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / "browser_test.html"
    source.write_text(args.viewer.read_text().replace("</body>", CHECK + "</body>"))
    command = [browser, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
               "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
               "--user-data-dir=/tmp/cfd3d-wind-browser-test", "--window-size=1800,1200",
               "--virtual-time-budget=45000", "--dump-dom",
               "--screenshot=" + str((args.output / "browser.png").resolve()), source.resolve().as_uri()]
    with (args.output / "browser_dom.html").open("w") as dom, (args.output / "browser.log").open("w") as log:
        subprocess.run(command, stdout=dom, stderr=log, check=True, timeout=120)
    output = (args.output / "browser_dom.html").read_text()
    match = re.search(r'<pre id="viewer-smoke-result"[^>]*>(.*?)</pre>', output, flags=re.S)
    if not match:
        raise SystemExit("Browser did not finish; inspect build/previews/wind_3d/browser.log")
    result = json.loads(html.unescape(match[1]))
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
