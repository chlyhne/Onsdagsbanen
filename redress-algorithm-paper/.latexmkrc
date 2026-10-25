use File::Path qw(make_path);
use File::Spec;
$out_dir = 'build/latex';
make_path("$out_dir/tikz");
my $separator = $^O eq 'MSWin32' ? ';' : ':';
$ENV{'TEXINPUTS'} = File::Spec->rel2abs($out_dir) . '//' . $separator . ($ENV{'TEXINPUTS'} // '');
$pdf_mode = 1;
