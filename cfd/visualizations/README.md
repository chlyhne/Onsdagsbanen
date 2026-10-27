# 3D-viser

Åbn `wind_3d.html` i browseren og vælg datasæt øverst.
Viseren ligger her uafhængigt af resultatmapperne og virker uden webserver.

Dataset switches preserve the selected field, view mode, slice height, opacity,
camera, axis ranges and aspect ratio. All datasets use a shared symmetric colour
scale spanning their saved calibration limits, with a common white band.
The underlying dataset values and height-dependent zero references are unchanged.

Horizontal cuts use a separate precomputed cache: 5 cm XY spacing around the
boat (x = -6…6 m, y = -3…3 m), at most 20 cm outside that region, and all 31
selectable heights from 0.5 to 15.5 m. Each layer is sampled directly in the
original CFD cells, with solids and missing samples left masked. The 4 m view
retains its original native triangulated cut. The coarse volume grid is unchanged.

`wind_slices.npz` and `wind_slices.json` retain the detailed numerical cache
and source fingerprints. `wind_slices.js` packages compressed fields for offline
browser loading. All field/height planes are decompressed and prepared in memory
before the viewer becomes ready; selecting a height needs no network request,
CFD sampling or decoding. Height changes update the plot in one rendering pass.
Keep this sidecar next to the dataset HTML when copying the standalone folder.

To build or reuse a detailed cache and publish it (post-processing only):

```powershell
python -m cfd.slice_cache --case cfd/output/trimmed_3d --viewer-dir cfd/output/trimmed_3d/visualizations
python -c "from pathlib import Path; from cfd.wind_viewer_3d import update_dataset_selectors; update_dataset_selectors(Path('cfd'))"
```

Normal `cfd.wind_viewer_3d` generation also prepares detailed slices. Use
`--from-cache` to rebuild only the HTML from existing caches.

Choose **Sail backflow** under **Show** to display resolved reverse flow as
one orange overlay. There are no side or sail selectors. The wind layers are
hidden in this view, and the camera is preserved when switching datasets.
Backflow is a separation indicator, not the full separated-flow region.
The velocity threshold is shown below the plot. Both current datasets use
0.05 m/s. Publishing reuses `separation_3d_both_sides` caches or the legacy
standalone HTML; it does not run a simulation or derive backflow from a 2D slice.

Optional browser regression check (requires Playwright and Microsoft Edge):

```powershell
python cfd/tests/browser_dataset_switch.py
```

Lokale datasæt publiceres i `datasets/`, når `python -m cfd.wind_viewer_3d`
køres. Denne mappe indeholder genererede HTML-filer og er ikke med i Git.
Eksisterende HTML-resultater i `cfd/output` og `cfd/results` medtages også,
selv når deres oprindelige CFD-data ikke er tilgængelige.

For kun at opdatere samlingen fra eksisterende HTML-resultater:

```powershell
python -c "from pathlib import Path; from cfd.wind_viewer_3d import update_dataset_selectors; update_dataset_selectors(Path('cfd'))"
```
