#!/usr/bin/python
import os, sys
import optparse
import hashlib
import pickle
import traceback
from sgftools import gotools, leela, annotations, progressbar

RESTART_COUNT=1

def graph_winrates(winrates, color, outp_fn):
    import matplotlib as mpl
    mpl.use('Agg')
    import matplotlib.pyplot as plt

    X = []
    Y = []
    for move_num in sorted(winrates.keys()):
        pl, wr = winrates[move_num]

        if pl != color:
            wr = 1. - wr
        X.append(move_num)
        Y.append(wr)

    plt.figure(1)
    plt.axhline(0.5, 0, max(winrates.keys()), linestyle='--', color='0.7')
    plt.plot(X, Y, color='k', marker='+')
    plt.xlim(0, max(winrates.keys()))
    plt.ylim(0, 1)
    plt.xlabel("Move Number", fontsize=28)
    plt.ylabel("Win Rate", fontsize=28)
    plt.savefig(outp_fn, dpi=200, format='pdf', bbox_inches='tight')

def retry_analysis(fn):
    global RESTART_COUNT
    def wrapped(*args, **kwargs):
        for i in xrange(RESTART_COUNT+1):
            try:
                return fn(*args, **kwargs)
            except Exception, e:
                if i+1 == RESTART_COUNT+1:
                    raise
                print >>sys.stderr, "Error in leela, retrying analysis..."
    return wrapped

@retry_analysis
def do_analyze(C, leela, pb, tries):
    leela.start()
    for i in xrange(tries):
        leela.reset()
        leela.goto_position()
        stats, move_list = leela.analyze()
    pb.increment()

    sorted_moves = sorted(move_list.keys(), key=lambda k: move_list[k]['visits'], reverse=True)
    sequences = [ explore_branch(leela, mv, options.depth, pb, tries) for mv in sorted_moves[:options.num_branches] ]
    leela.stop()

    return stats, move_list, sequences

@retry_analysis
def do_suggest(C, leela, pb, tries):
    leela.start()
    for i in xrange(tries):
        leela.reset()
        leela.goto_position()
        stats, move_list = leela.analyze()
    pb.increment()

    leela.stop()

    return stats, move_list

def explore_branch(leela, mv, depth, pb, tries):
    seq = []

    for i in xrange(depth):
        color = leela.whoseturn()
        leela.add_move(color, mv)

        for j in xrange(tries):
            leela.reset()
            leela.goto_position()
            stats, move_list = leela.analyze()

        pb.increment()
        seq.append( (color, mv, stats, move_list) )
        mv = stats['chosen']

    for i in xrange(depth):
        leela.pop_move()

    return seq

def calculate_task_size(sgf, branches, depth, start_m, end_n):
    C = sgf.cursor()
    move_num=0
    steps=0
    while not C.atEnd:
        C.next()

        analysis_mode = None
        if move_num >= options.analyze_after_m_moves and move_num < options.analyze_first_n_moves:
            analysis_mode='suggest'

        if 'C' in C.node.keys():
            if 'variations' in C.node['C'].data[0]:
                analysis_mode='variations'
            elif 'suggest' in C.node['C'].data[0]:
                analysis_mode='suggest'

        if analysis_mode=='suggest':
            steps+=1
        elif analysis_mode=='variations':
            steps+=1+depth*branches

        move_num+=1
    return steps

if __name__=='__main__':
    parser = optparse.OptionParser()
    parser.add_option('-b', '--branches', dest='num_branches', default=2, type=int, metavar="N",
                      help="Explore the top N branches from the main line at each analysis (default=2)")
    parser.add_option('-d', '--depth', dest='depth', default=5, type=int, metavar="D",
                      help="Explore variations to depth D (default=5)")

    parser.add_option('-m', '--after-m', dest='analyze_after_m_moves', default=0, type=int,
                      help="Suggest moves starting after the first M moves (default=0)", metavar="M")
    parser.add_option('-n', '--first-n', dest='analyze_first_n_moves', default=0, type=int,
                      help="Suggest moves for all of the first N moves (default=0)", metavar="N")

    parser.add_option('-p', '--player', dest='player_color', 
                      help="Set the player color to focus on during analysis")
    parser.add_option('-g', '--win-graph', dest='win_graph',
                      help="Graph the win rate of the selected player (Requires a move range with -m and -n)")

    parser.add_option('-s', '--supplement', dest='analyze_same_position', metavar="R",
                      default=1, type=int, help="Analyze the same position R times to generate a more thorough analysis")
    parser.add_option('-l', '--limit', dest='eval_limit', default=None, type=int,
                      help="Limit the number of evaluations (default: Unlimited)")

    parser.add_option('-v', '--verbosity', default=0, type=int,
                      help="Set the verbosity level, 0: progress only, 1: progress+status, 2: progress+status+state")
    parser.add_option('-x', '--executable', default='leela_090_macOS_opencl', 
                      help="Set the default executable name for the leela command line engine")
    parser.add_option('-c', '--checkpoint-directory', dest='ckpt_dir',
                      default=os.path.expanduser('~/.leela_checkpoints'),
                      help="Set a directory to store partially complete analyses")
    parser.add_option('-r', '--restarts', default=1, type=int,
                      help="If leela crashes, retry the analysis step this many times before reporting a failure")

    options, args = parser.parse_args()
    sgf_fn = args[0]
    if not os.path.exists(sgf_fn):
        parser.error("No such file: %s" % (sgf_fn))
    sgf = gotools.import_sgf(sgf_fn)

    if options.player_color not in ['black', 'white']:
        parser.error("Player color must be one of black or white")
    if options.win_graph and not options.player_color:
        parser.error("Win graph option -g requires specifying the player of interest with -p")

    RESTART_COUNT = options.restarts

    if not os.path.exists( options.ckpt_dir ):
        os.mkdir( options.ckpt_dir )
    base_hash = hashlib.md5( os.path.abspath(sgf_fn) ).hexdigest()
    base_dir = os.path.join(options.ckpt_dir, base_hash)
    if not os.path.exists( base_dir ):
        os.mkdir( base_dir )
    if options.verbosity > 1:
        print >>sys.stderr, "Checkpoint dir:", base_dir

    C = sgf.cursor()

    if 'SZ' in C.node.keys():
        SZ = int(C.node['SZ'].data[0])
    else:
        SZ = 19

    analysis_num = 0
    move_num = 0
    task_size=calculate_task_size(sgf, options.num_branches, options.depth, options.analyze_after_m_moves, options.analyze_first_n_moves)
    print >>sys.stderr, "Executing %d analysis steps" % (task_size)
    pb = progressbar.ProgressBar(max_value=task_size)
    pb.start()

    leela = leela.CLI(board_size=SZ, eval_limit=options.eval_limit,
                          executable=options.executable,
                          verbosity=options.verbosity)

    collected_winrates = {}

    try:
        while not C.atEnd:
            C.next()
            if 'W' in C.node.keys():
                leela.add_move('white', C.node['W'].data[0])
            if 'B' in C.node.keys():
                leela.add_move('black', C.node['B'].data[0])

            analysis_mode = None
            current_player = leela.whoseturn()
            if move_num >= options.analyze_after_m_moves and move_num < options.analyze_first_n_moves:
                analysis_mode='suggest'

            if 'C' in C.node.keys():
                if 'variations' in C.node['C'].data[0]:
                    analysis_mode='variations'
                elif 'suggest' in C.node['C'].data[0]:
                    analysis_mode='suggest'

            if analysis_mode=='variations':
                ckpt_hash = ('analyze_%d_%d_' + leela.history_hash()) % (options.num_branches, options.depth)
                ckpt_fn = os.path.join(base_dir, ckpt_hash)
                if options.verbosity > 2:
                    print >>sys.stderr, "Looking for checkpoint file:", ckpt_fn
                if os.path.exists(ckpt_fn):
                    if options.verbosity > 1:
                        print >>sys.stderr, "Loading checkpoint file:", ckpt_fn
                    with open(ckpt_fn, 'r') as ckpt_file:
                        stats, move_list, sequences = pickle.load(ckpt_file)
                    pb.increment( 1+sum(len(seq) for seq in sequences) )
                else:
                    stats, move_list, sequences = do_analyze(C, leela, pb, options.analyze_same_position)

                if 'winrate' in stats and stats['visits'] > 1000:
                    collected_winrates[move_num] = (current_player, stats['winrate'])

                annotations.format_analysis(C, leela.whoseturn(), stats, move_list)
                for seq in sequences:
                    annotations.format_variation(C, seq)

                with open(ckpt_fn, 'w') as ckpt_file:
                    pickle.dump((stats, move_list, sequences), ckpt_file)

            elif analysis_mode=='suggest':
                ckpt_hash = 'suggest_' + leela.history_hash()
                ckpt_fn = os.path.join(base_dir, ckpt_hash)
                if options.verbosity > 2:
                    print >>sys.stderr, "Looking for checkpoint file:", ckpt_fn
                if os.path.exists(ckpt_fn):
                    if options.verbosity > 1:
                        print >>sys.stderr, "Loading checkpoint file:", ckpt_fn
                    with open(ckpt_fn, 'r') as ckpt_file:
                        stats, move_list = pickle.load(ckpt_file)
                        pb.increment()
                else:
                    stats, move_list = do_suggest(C, leela, pb, options.analyze_same_position)

                if 'winrate' in stats and stats['visits'] > 1000:
                    collected_winrates[move_num] = (current_player, stats['winrate'])

                annotations.format_analysis(C, leela.whoseturn(), stats, move_list)

                with open(ckpt_fn, 'w') as ckpt_file:
                    pickle.dump((stats, move_list), ckpt_file)

            move_num+=1
    except:
        print >>sys.stderr, "Failure in leela, reporting partial results...\n"
        if options.verbosity > 0:
            traceback.print_exc()

    if options.win_graph:
        graph_winrates(collected_winrates, options.player_color, options.win_graph)

    pb.finish()
    print sgf
