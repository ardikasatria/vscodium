%% tasainsdata.cls — Format Penulisan Tugas Akhir Program Studi Sains Data ITERA
%% Semua pengaturan format ada di berkas ini. Isi tugas akhir ditulis di main.tex
%% dan di folder awal/, bab/, akhir/. Jangan mengubah berkas ini kecuali format
%% resminya berubah.
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{tasainsdata}[2026/10/10 Format TA Sains Data ITERA]
\LoadClass[12pt,a4paper,oneside]{book}

%% Package
\usepackage[utf8]{inputenc}
\usepackage{mathptmx, times}
\usepackage{amsmath, amsfonts, amssymb, amsthm}
\usepackage{thmtools,blindtext}
\declaretheoremstyle[%
spaceabove=1pt,%reduce or increase between theorem and proof
spacebelow=5pt,%reduce or increase
headfont=\normalfont\itshape,%
postheadspace=1em,%
qed=\qedsymbol%
]{mystyle} 
\declaretheorem[name={Bukti},style=mystyle,unnumbered,
]{pf}

\usepackage{graphicx, makeidx}
\usepackage{layout}
\usepackage{titlesec}
\usepackage{lipsum}
\usepackage[left=3.00cm, right=3.00cm, top=3.00cm, bottom=3.00cm]{geometry}
\usepackage[hidelinks]{hyperref}% tautan tetap bisa diklik, tanpa kotak/garis berwarna di PDF
\usepackage{pgf,tikz}
\usepackage{mathrsfs}
\usetikzlibrary{arrows}
\usepackage{setspace}
\usepackage{cleveref}
\usepackage{enumitem}
\usepackage{fancyhdr}
\usepackage{multirow}
\usepackage{booktabs}
\usepackage{tabularx}
\usepackage{eucal}
\usepackage[Algorithm,ruled]{algorithm}
\usepackage{algpseudocode}
\usepackage{algorithmicx}
%\usepackage{algorithm,algorithmic}
\usepackage{rotating}
%\usepackage{sectsty}
\usepackage{booktabs}
\usepackage{amsthm}
\usepackage{titlesec}
\usepackage{multirow}
%\usepackage{filecontents}
\usepackage{titletoc}% http://ctan.org/pkg/titletoc
\usepackage[bahasa]{babel}
\usepackage[font=small]{caption}
%\usepackage[justification=centering]{caption}
\usepackage{subcaption}
\usepackage{float}
\usepackage{etoolbox}
\usepackage{ragged2e}
\usepackage{placeins}
\usepackage{breqn}

\usepackage{chngcntr}
\counterwithout{subsubsection}{subsection}

\captionsetup{format=hang}
%daftar pustaka
\renewcommand\@biblabel[1]{}

\titlecontents{chapter}% <section-type>
[0pt]% <left>
{\addvspace{1em}}% <above-code>
{\bfseries\chaptername\ \thecontentslabel\quad}% <numbered-entry-format>
{}% <numberless-entry-format>
{\bfseries\hfill\contentspage}% <filler-page-format>

\titleformat{\chapter}
{\filcenter\normalfont\large\bfseries}
{\chaptertitlename~\thechapter} {0.5em} {}
\renewcommand \thechapter{\Roman{chapter}}

\titlespacing{\chapter}{0pt}{0pt}{16pt}
\titlespacing{\section}{-5pt}{6pt}{-5pt}
\titlespacing{\subsection}{-5pt}{6pt}{-5pt}
\titlespacing{\subsubsection}{0pt}{6pt}{-5pt}

\setlength{\belowcaptionskip}{-10pt}

%\usepackage{blindtext, subfig}
%\usepackage{dblfloatfix}
%\captionsetup{compatibility=false}

\pagestyle{fancy}
\fancyhf{}

\onehalfspacing
\setlength{\parskip}{1em}
%\setlength{\parsep}{0em}
\setlength{\parindent}{0em}
%\setlist{nosep}

%\titlespacing*{\section}{0pt}{12pt plus 4pt minus 2pt}{0pt plus 2pt minus 2pt}
\titleformat{\section}{\normalfont\bfseries}{\sectiontitle~\thesection} {0.5em} {}
\titleformat{\subsection}{\normalfont\bfseries}{\sectiontitle~\thesubsection} {0.5em} {} {}

\frenchspacing

\theoremstyle{plain}
\newtheorem{lemma}{Lemma}[section]
\newtheorem{definisi}{Definisi}[section]
\newtheorem{akibat}{Akibat}[section]
\newtheorem{teorema}{Teorema}[section]
\newtheorem{bukti}{Bukti}[section]
\newtheorem{thm}{Teorema}[subsection]
\newtheorem{proposisi}{Proposisi}[section]
\newtheorem{observasi}{Observasi}[section]
\newtheorem{dugaan}{Dugaan}[section]
\newtheorem{masalah}{Masalah}[section]

\newenvironment{breakablealgorithm}
{% \begin{breakablealgorithm}
	\begin{center}
		\refstepcounter{algorithm}% New algorithm
		\hrule height.1pt depth.1pt \kern-2pt% \@fs@pre for \@fs@ruled
		\renewcommand{\caption}[2][\relax]{% Make a new \caption
			{\raggedright\textbf{Algoritma~\thealgorithm} ##2}%
			\ifx\relax##1\relax % #1 is \relax
			\addcontentsline{loa}{algorithm}{\protect\numberline{\thealgorithm}##2}%
			\else % #1 is not \relax
			\addcontentsline{loa}{algorithm}{\protect\numberline{\thealgorithm}##1}%
			\fi
			\kern2pt\hrule\kern2pt
		}
	}{% \end{breakablealgorithm}
		\kern2pt\hrule\relax% \@fs@post for \@fs@ruled
	\end{center}
}

\newcommand{\overbar}[1]{\mkern 1.5mu\overline{\mkern-1.5mu#1\mkern-1.5mu}\mkern 1.5mu}


%% Title

\newcommand{\listexamplename}{DAFTAR LAMPIRAN}
%\newlistof{example}{exp}{\listexamplename}
\newcommand{\lampiran}[1]{%
	\refstepcounter{example}
	\par\noindent\textbf{LAMPIRAN  \theexample\quad #1}
	\addcontentsline{exp}{example}
	{{\theexample} #1}\par}

\makeindex

% Dipakai \titleformat di atas; pada format asli perintah ini tidak pernah
% didefinisikan (galat "Undefined control sequence" tiap \section).
\providecommand{\sectiontitle}{}

% Gambar dicari di folder gambar/
\graphicspath{{gambar/}}

% \masuk{folder/berkas}: memuat satu bagian pada halaman baru. Berbeda dari
% \include, tidak membuat berkas .aux tersendiri untuk tiap bagian.
\newcommand{\masuk}[1]{\clearpage\input{#1}\clearpage}

%% Halaman daftar ----------------------------------------------------------
\newcommand{\daftarisi}{%
	\thispagestyle{myheadings}
	\renewcommand{\contentsname}{DAFTAR ISI}
	\pagestyle{fancy}
	\newpage
	\addcontentsline{toc}{chapter}{\textbf{DAFTAR ISI}}
	\tableofcontents
	\fancyhead[L]{\textsl{Daftar Isi}}
	\fancyhead[R]{}
	\fancyfoot[C]{\thepage}
}
\newcommand{\daftargambar}{%
	\parindent 0cm
	\renewcommand*\l@figure{\@dottedtocline{1}{1em}{3.2em}}
	\renewcommand{\listfigurename}{DAFTAR GAMBAR}
	\listoffigures
	\addcontentsline{toc}{chapter}{\textbf{DAFTAR GAMBAR}}
	\pagestyle{fancy}
	\fancyhead[L]{\textsl{Daftar Gambar}}
	\fancyhead[R]{}
	\fancyfoot[C]{\thepage}
}
\newcommand{\daftartabel}{%
	\renewcommand{\listtablename}{DAFTAR TABEL}
	\renewcommand*\l@table{\@dottedtocline{1}{1em}{3.2em}}
	\listoftables
	\addcontentsline{toc}{chapter}{\textbf{DAFTAR TABEL}}
	\pagestyle{fancy}
	\fancyhead[L]{\textsl{Daftar Tabel}}
	\fancyhead[R]{}
	\fancyfoot[C]{\thepage}
}
\newcommand{\daftaralgoritma}{%
	\renewcommand{\listofalgorithms}{%
		\begingroup
		\listof{algorithm}{DAFTAR ALGORITMA}
		\endgroup
	}
	\renewcommand*\l@table{\@dottedtocline{1}{1em}{3.2em}}
	\listofalgorithms
	\addcontentsline{toc}{chapter}{\textbf{DAFTAR ALGORITMA}}
	\pagestyle{fancy}
	\fancyhead[L]{\textsl{Daftar Algoritma}}
	\fancyhead[R]{}
	\fancyfoot[C]{\thepage}
}

\pagenumbering{roman}
\tolerance=1
\emergencystretch=\maxdimen
\hyphenpenalty=1000
\hbadness=1000
\endinput
