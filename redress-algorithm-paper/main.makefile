ALL_FIGURE_NAMES=$(shell cat main.figlist)
ALL_FIGURES=$(ALL_FIGURE_NAMES:%=%.pdf)

allimages: $(ALL_FIGURES)
	@echo All images exist now. Use make -B to re-generate them.

FORCEREMAKE:

-include $(ALL_FIGURE_NAMES:%=%.dep)

%.dep:
	mkdir -p "$(dir $@)"
	touch "$@" # will be filled later.

tikzcache/ml-fit-example-cdf.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/ml-fit-example-cdf" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/ml-fit-example-cdf.pdf: tikzcache/ml-fit-example-cdf.md5
tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_rikke-m-schnuchel_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_rikke-m-schnuchel_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_rikke-m-schnuchel_percent.pdf: tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_rikke-m-schnuchel_percent.md5
tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_axel-thomsen_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_axel-thomsen_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_axel-thomsen_percent.pdf: tikzcache/dualpercent-boat_plot_casper-lyhne_percent-vs-boat_plot_axel-thomsen_percent.md5
tikzcache/dualpercent-boat_plot_christian-grejs_percent-vs-boat_plot_axel-thomsen_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/dualpercent-boat_plot_christian-grejs_percent-vs-boat_plot_axel-thomsen_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/dualpercent-boat_plot_christian-grejs_percent-vs-boat_plot_axel-thomsen_percent.pdf: tikzcache/dualpercent-boat_plot_christian-grejs_percent-vs-boat_plot_axel-thomsen_percent.md5
tikzcache/xgamma-stor_bane_x_gamma_trajectories_2025_2026_manifest.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/xgamma-stor_bane_x_gamma_trajectories_2025_2026_manifest" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/xgamma-stor_bane_x_gamma_trajectories_2025_2026_manifest.pdf: tikzcache/xgamma-stor_bane_x_gamma_trajectories_2025_2026_manifest.md5
tikzcache/boatplot-boat_plot_casper-lyhne_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_casper-lyhne_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_casper-lyhne_percent.pdf: tikzcache/boatplot-boat_plot_casper-lyhne_percent.md5
tikzcache/boatplot-boat_plot_axel-thomsen_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_axel-thomsen_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_axel-thomsen_percent.pdf: tikzcache/boatplot-boat_plot_axel-thomsen_percent.md5
tikzcache/boatplot-boat_plot_blue-x_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_blue-x_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_blue-x_percent.pdf: tikzcache/boatplot-boat_plot_blue-x_percent.md5
tikzcache/boatplot-boat_plot_bo-boddum_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_bo-boddum_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_bo-boddum_percent.pdf: tikzcache/boatplot-boat_plot_bo-boddum_percent.md5
tikzcache/boatplot-boat_plot_christian-grejs_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_christian-grejs_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_christian-grejs_percent.pdf: tikzcache/boatplot-boat_plot_christian-grejs_percent.md5
tikzcache/boatplot-boat_plot_christian-lindholst_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_christian-lindholst_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_christian-lindholst_percent.pdf: tikzcache/boatplot-boat_plot_christian-lindholst_percent.md5
tikzcache/boatplot-boat_plot_rikke-m-schnuchel_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_rikke-m-schnuchel_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_rikke-m-schnuchel_percent.pdf: tikzcache/boatplot-boat_plot_rikke-m-schnuchel_percent.md5
tikzcache/boatplot-boat_plot_thomas-pedersen_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_thomas-pedersen_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_thomas-pedersen_percent.pdf: tikzcache/boatplot-boat_plot_thomas-pedersen_percent.md5
tikzcache/boatplot-boat_plot_thomas-yde-2_percent.pdf: 
	pdflatex -shell-escape -halt-on-error -interaction=batchmode -jobname "tikzcache/boatplot-boat_plot_thomas-yde-2_percent" "\def\tikzexternalrealjob{main}\input{main}"

tikzcache/boatplot-boat_plot_thomas-yde-2_percent.pdf: tikzcache/boatplot-boat_plot_thomas-yde-2_percent.md5
