from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 browser=p.chromium.launch(channel='msedge',headless=True,args=['--allow-file-access-from-files'])
 page=browser.new_page()
 errors=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.goto(Path('cfd/visualizations/wind_3d.html').resolve().as_uri())
 def frame_ready():
  frame=page.frames[1]
  frame.wait_for_function('window.viewerReady===true',timeout=90000)
  frame.wait_for_function("document.getElementById('reference').textContent.startsWith('Shared colour')",timeout=30000)
  return frame
 frame=frame_ready()
 assert frame.evaluate("window.sliceCacheState.ready && window.sliceCacheState.planes===93 && window.sliceCacheState.width>400 && window.sliceCacheState.rows>400")
 assert frame.evaluate("gridPlane('goodness',6)===gridPlane('goodness',6) && gridPlane('goodness',6).surfacecolor.length>400")
 print('PASS: all 93 detailed field/height planes preloaded before selection',flush=True)
 # Exercise real pointer rotation from the exact top-down camera.
 frame.evaluate("document.getElementById('topView').onclick()")
 assert frame.evaluate("gd._fullLayout.scene.dragmode==='orbit'")
 box=frame.locator('#plot').bounding_box()
 x,y=box['x']+box['width']*.45,box['y']+box['height']*.45
 cameras=[]
 for dx,dy in [(100,65),(-75,90)]:
  page.mouse.move(x,y)
  page.mouse.down()
  page.mouse.move(x+dx,y+dy,steps=15)
  page.mouse.up()
  page.wait_for_timeout(500)
  cameras.append(frame.evaluate('captureView().camera'))
 assert cameras[0]!=cameras[1],cameras
 import math
 for camera in cameras:
  eye,up=camera['eye'],camera['up']
  assert all(math.isfinite(v) for vector in (eye,up) for v in vector.values())
  cross=[eye['y']*up['z']-eye['z']*up['y'],eye['z']*up['x']-eye['x']*up['z'],eye['x']*up['y']-eye['y']*up['x']]
  assert sum(v*v for v in cross)>1e-6,camera
 assert abs(cameras[0]['eye']['x'])+abs(cameras[0]['eye']['y'])>1e-3
 frame.evaluate('''async()=>{
 document.getElementById('field').value='direction';await document.getElementById('field').onchange({target:document.getElementById('field')});
 document.getElementById('height').value='6';await document.getElementById('height').onchange({target:document.getElementById('height')});
 await setMode('both');document.getElementById('opacity').value='.31';await document.getElementById('opacity').onchange({target:document.getElementById('opacity')});
 await Plotly.relayout(gd,{'scene.camera':{eye:{x:1.4,y:-1.2,z:1.8},up:{x:0,y:0,z:1},projection:{type:'orthographic'}},'scene.xaxis.range':[-12,7]});
 }''')
 before=frame.evaluate('captureView()')
 timing=frame.evaluate("""async()=>{
 const start=performance.now();height=6.5;await refresh();
 const elapsed=performance.now()-start;
 const reused=gd.data[planeIndex].surfacecolor.length===sliceAxes.y.length;
 height=6;await refresh();return {milliseconds:elapsed,reused};
 }""")
 assert timing['reused'],timing
 print('Cached slice change:',timing,flush=True)
 colors=frame.evaluate('({min:gd.data[volumeIndex].cmin,max:gd.data[volumeIndex].cmax,palette:gd.data[volumeIndex].colorscale})')
 page.select_option('#dataset',index=1)
 page.wait_for_timeout(500)
 frame=frame_ready()
 frame.wait_for_function("window.viewerState.height===6 && window.viewerState.field==='direction'",timeout=30000)
 frame.wait_for_timeout(1500)
 after=frame.evaluate('captureView()')
 assert before==after,(before,after)
 assert colors==frame.evaluate('({min:gd.data[volumeIndex].cmin,max:gd.data[volumeIndex].cmax,palette:gd.data[volumeIndex].colorscale})')
 frame.evaluate("""async()=>{
 document.getElementById('field').value='backflow';await document.getElementById('field').onchange({target:document.getElementById('field')});
 }""")
 assert frame.evaluate("backflowIndices.length===4 && backflowIndices.every(i=>gd.data[i].visible && gd.data[i].color==='#df7026' && gd.data[i].name==='Backflow') && !gd.data[volumeIndex].visible")
 before=frame.evaluate('captureView()')
 page.select_option('#dataset',index=0)
 page.wait_for_timeout(500)
 frame=page.frames[1]
 frame.wait_for_function("window.viewerReady && window.viewerState.field==='backflow'",timeout=90000)
 frame.wait_for_timeout(1500)
 assert before==frame.evaluate('captureView()')
 assert frame.evaluate("backflowIndices.length===4 && backflowIndices.every(i=>gd.data[i].visible && gd.data[i].color==='#df7026')")
 frame.evaluate("""async()=>{document.getElementById('field').value='goodness';await document.getElementById('field').onchange({target:document.getElementById('field')});}""")
 assert frame.evaluate("backflowIndices.every(i=>!gd.data[i].visible) && gd.data[volumeIndex].visible && window.viewerState.height===6")
 assert not errors,errors
 print('PASS: free rotation from top view; dataset switches preserve view settings and backflow')
 browser.close()
