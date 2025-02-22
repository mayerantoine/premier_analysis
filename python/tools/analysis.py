import pandas as pd
import numpy as np
import os

from sklearn.metrics import confusion_matrix
from sklearn.metrics import roc_auc_score, average_precision_score
from scipy.stats import chi2, norm
from copy import deepcopy


# Quick function for thresholding probabilities
def threshold(probs, cutoff=.5):
    return np.array(probs >= cutoff).astype(np.uint8)


# Calculates McNemar's chi-squared statistic
def mcnemar_test(true, pred, cc=True):
    cm = confusion_matrix(true, pred)
    b = int(cm[0, 1])
    c = int(cm[1, 0])
    if cc:
        stat = (abs(b - c) - 1)**2 / (b + c)
    else:
        stat = (b - c)**2 / (b + c)
    p = 1 - chi2(df=1).cdf(stat)
    outmat = np.array([b, c, stat, p]).reshape(-1, 1)
    out = pd.DataFrame(outmat.transpose(), columns=['b', 'c', 'stat', 'pval'])
    return out


# Calculates the Brier score for multiclass problems
def brier_score(true, pred):
    n_classes = len(np.unique(true))
    if n_classes == 2:
        pred = pred.flatten()
        bs = np.sum((pred - true)**2) / true.shape[0]
    else:
        y = onehot_matrix(true)
        row_diffs = np.diff((pred, y), axis=0)[0]
        squared_diffs = row_diffs**2
        row_sums = np.sum(squared_diffs, axis=1)
        bs = row_sums.mean()
    return bs


# Runs basic diagnostic stats on categorical predictions
def clf_metrics(true,
                pred,
                average='weighted',
                cutpoint=0.5,
                mod_name=None,
                round=4,
                round_pval=False,
                mcnemar=False,
                argmax_axis=1):
    '''Runs basic diagnostic stats on binary (only) predictions'''

    averaging_options = ["weighted", "macro", "micro"]

    if average not in averaging_options:
        raise ValueError(
            "value of 'average' is not a valid choice in {}".format(
                averaging_options))

    # Converting pd.Series to np.array
    stype = type(pd.Series([0]))
    if type(pred) == stype:
        pred = pred.values
    if type(true) == stype:
        true = true.values

    # Figuring out if the guesses are classes or probabilities
    if np.any([0 < p < 1 for p in pred.flatten()]):
        preds_are_probs = True
    else:
        preds_are_probs = False

    # Optional exit for doing averages with multiclass/label inputs
    if len(np.unique(true)) > 2:
        # Getting binary metrics for each set of results
        codes = np.unique(true)

        # Softmaxing the probabilities if it hasn't already been done
        if np.sum(pred[0]) > 1:
            pred = np.array([np.exp(p) / np.sum(np.exp(p)) for p in pred])

        # Argmaxing for when we have probabilities
        if preds_are_probs:
            auc = roc_auc_score(true, pred, average=average, multi_class='ovr')
            brier = brier_score(true, pred)
            pred = np.argmax(pred, axis=argmax_axis)

        # Making lists of the binary predictions (OVR)
        y = [
            np.array([doc == code for doc in true], dtype=np.uint8)
            for code in codes
        ]
        y_ = [
            np.array([doc == code for doc in pred], dtype=np.uint8)
            for code in codes
        ]

        # Getting the stats for each set of binary predictions
        stats = [clf_metrics(y[i], y_[i], round=16) for i in range(len(y))]
        stats = pd.concat(stats, axis=0)
        stats.fillna(0, inplace=True)
        cols = stats.columns.values

        # Calculating the averaged metrics
        if average == 'weighted':
            weighted = np.average(stats, weights=stats.true_prev, axis=0)
            out = pd.DataFrame(weighted).transpose()
            out.columns = cols
        elif average == 'macro':
            out = pd.DataFrame(stats.mean()).transpose()
        elif average == 'micro':
            out = clf_metrics(np.concatenate(y), np.concatenate(y_))

        # Adding AUC and AP for when we have probabilities
        if preds_are_probs:
            out.auc = auc
            out.brier = brier

        # Rounding things off
        out = out.round(round)
        count_cols = [
            'tp', 'fp', 'tn', 'fn', 'true_prev', 'pred_prev', 'prev_diff'
        ]
        out[count_cols] = out[count_cols].round()

        if mod_name is not None:
            out['model'] = mod_name

        return out

    # Thresholding the probabilities, if provided
    if preds_are_probs:
        auc = roc_auc_score(true, pred)
        brier = brier_score(true, pred)
        ap = average_precision_score(true, pred)
        pred = threshold(pred, cutpoint)
    else:
        brier = np.round(brier_score(true, pred), round)

    # Constructing the 2x2 table
    confmat = confusion_matrix(true, pred)
    tp = confmat[1, 1]
    fp = confmat[0, 1]
    tn = confmat[0, 0]
    fn = confmat[1, 0]

    # Calculating the main binary metrics
    ppv = np.round(tp / (tp + fp), round) if tp + fp > 0 else 0
    sens = np.round(tp / (tp + fn), round) if tp + fn > 0 else 0
    spec = np.round(tn / (tn + fp), round) if tn + fp > 0 else 0
    npv = np.round(tn / (tn + fn), round) if tn + fn > 0 else 0
    f1 = np.round(2 * (sens * ppv) /
                  (sens + ppv), round) if sens + ppv != 0 else 0

    # Calculating the Matthews correlation coefficient
    mcc_num = ((tp * tn) - (fp * fn))
    mcc_denom = np.sqrt(((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)))
    mcc = mcc_num / mcc_denom if mcc_denom != 0 else 0

    # Calculating Youden's J and the Brier score
    j = sens + spec - 1

    # Rolling everything so far into a dataframe
    outmat = np.array(
        [tp, fp, tn, fn, sens, spec, ppv, npv, j, f1, mcc,
         brier]).reshape(-1, 1)
    out = pd.DataFrame(outmat.transpose(),
                       columns=[
                           'tp', 'fp', 'tn', 'fn', 'sens', 'spec', 'ppv',
                           'npv', 'j', 'f1', 'mcc', 'brier'
                       ])

    # Optionally tacking on stats from the raw probabilities
    if preds_are_probs:
        out['auc'] = auc
        out['ap'] = ap
    else:
        out['auc'] = 0.0
        out['ap'] = 0.0

    # Calculating some additional measures based on positive calls
    true_prev = int(np.sum(true == 1))
    pred_prev = int(np.sum(pred == 1))
    abs_diff = (true_prev - pred_prev) * -1
    rel_diff = np.round(abs_diff / true_prev, round)
    if mcnemar:
        pval = mcnemar_test(true, pred).pval[0]
        if round_pval:
            pval = np.round(pval, round)
    count_outmat = np.array([true_prev, pred_prev, abs_diff,
                             rel_diff]).reshape(-1, 1)
    count_out = pd.DataFrame(
        count_outmat.transpose(),
        columns=['true_prev', 'pred_prev', 'prev_diff', 'rel_prev_diff'])
    out = pd.concat([out, count_out], axis=1)

    # Optionally dropping the mcnemar p-val
    if mcnemar:
        out['mcnemar'] = pval

    # And finally tacking on the model name
    if mod_name is not None:
        out['model'] = mod_name

    return out


def jackknife_metrics(targets, guesses, cutpoint=0.5, average='weighted'):
    # Replicates of the dataset with one row missing from each
    rows = np.array(list(range(targets.shape[0])))
    j_rows = [np.delete(rows, row) for row in rows]

    # using a pool to get the metrics across each
    scores = [
        clf_metrics(targets[idx],
                    guesses[idx],
                    cutpoint=cutpoint,
                    average=average) for idx in j_rows
    ]
    scores = pd.concat(scores, axis=0)
    means = scores.mean()

    return scores, means


# Calculates bootstrap confidence intervals for an estimator
class boot_cis:
    def __init__(self,
                 targets,
                 guesses,
                 n=100,
                 a=0.05,
                 method="bca",
                 interpolation="nearest",
                 average='weighted',
                 cutpoint=0.5,
                 mcnemar=False,
                 seed=10221983):
        # Converting everything to NumPy arrays, just in case
        stype = type(pd.Series())
        if type(targets) == stype:
            targets = targets.values
        if type(guesses) == stype:
            guesses = guesses.values

        # Getting the point estimates
        stat = clf_metrics(targets,
                           guesses,
                           cutpoint=cutpoint,
                           average=average,
                           mcnemar=mcnemar).transpose()

        # Pulling out the column names to pass to the bootstrap dataframes
        colnames = list(stat.index.values)

        # Making an empty holder for the output
        scores = pd.DataFrame(np.zeros(shape=(n, stat.shape[0])),
                              columns=colnames)

        # Setting the seed
        if seed is None:
            seed = np.random.randint(0, 1e6, 1)
        np.random.seed(seed)
        seeds = np.random.randint(0, 1e6, n)

        # Generating the bootstrap samples and metrics
        boots = [boot_sample(targets, seed=seed) for seed in seeds]
        scores = [
            clf_metrics(targets[b],
                        guesses[b],
                        cutpoint=cutpoint,
                        average=average) for b in boots
        ]
        scores = pd.concat(scores, axis=0)

        # Calculating the confidence intervals
        lower = (a / 2) * 100
        upper = 100 - lower

        # Making sure a valid method was chosen
        methods = ["pct", "diff", "bca"]
        assert method in methods, "Method must be pct, diff, or bca."

        # Calculating the CIs with method #1: the percentiles of the
        # bootstrapped statistics
        if method == "pct":
            cis = np.nanpercentile(scores,
                                   q=(lower, upper),
                                   interpolation=interpolation,
                                   axis=0)
            cis = pd.DataFrame(cis.transpose(),
                               columns=["lower", "upper"],
                               index=colnames)

        # Or with method #2: the percentiles of the difference between the
        # obesrved statistics and the bootstrapped statistics
        elif method == "diff":
            stat_vals = stat.transpose().values.ravel()
            diffs = stat_vals - scores
            percents = np.nanpercentile(diffs,
                                        q=(lower, upper),
                                        interpolation=interpolation,
                                        axis=0)
            lower_bound = pd.Series(stat_vals + percents[0])
            upper_bound = pd.Series(stat_vals + percents[1])
            cis = pd.concat([lower_bound, upper_bound], axis=1)
            cis = cis.set_index(stat.index)

        # Or with method #3: the bias-corrected and accelerated bootstrap
        elif method == "bca":
            # Calculating the bias-correction factor
            stat_vals = stat.transpose().values.ravel()
            n_less = np.sum(scores < stat_vals, axis=0)
            p_less = n_less / n
            z0 = norm.ppf(p_less)

            # Fixing infs in z0
            z0[np.where(np.isinf(z0))[0]] = 0.0

            # Estiamating the acceleration factor
            j = jackknife_metrics(targets=targets,
                                  guesses=guesses,
                                  cutpoint=cutpoint,
                                  average=average)
            diffs = j[1] - j[0]
            numer = np.sum(np.power(diffs, 3))
            denom = 6 * np.power(np.sum(np.power(diffs, 2)), 3 / 2)

            # Getting rid of 0s in the denominator
            zeros = np.where(denom == 0)[0]
            for z in zeros:
                denom[z] += 1e-6

            # Finishing up the acceleration parameter
            acc = numer / denom
            self.jack = j

            # Calculating the bounds for the confidence intervals
            zl = norm.ppf(a / 2)
            zu = norm.ppf(1 - (a / 2))
            lterm = (z0 + zl) / (1 - acc * (z0 + zl))
            uterm = (z0 + zu) / (1 - acc * (z0 + zu))
            lower_q = norm.cdf(z0 + lterm) * 100
            upper_q = norm.cdf(z0 + uterm) * 100
            self.lower_q = lower_q
            self.upper_q = upper_q

            # Returning the CIs based on the adjusted quintiles
            cis = [
                np.nanpercentile(
                    scores.iloc[:, i],
                    q=(lower_q[i], upper_q[i]),
                    interpolation=interpolation,
                    axis=0,
                ) for i in range(len(lower_q))
            ]
            cis = pd.DataFrame(cis, columns=["lower", "upper"], index=colnames)

        # Putting the stats with the lower and upper estimates
        cis = pd.concat([stat, cis], axis=1)
        cis.columns = ["stat", "lower", "upper"]

        # Passing the results back up to the class
        self.cis = cis
        self.scores = scores

        return


def average_pvals(p_vals,
                  w=None,
                  method='harmonic',
                  smooth=True,
                  smooth_val=1e-7):
    if smooth:
        p = p_vals + smooth_val
    else:
        p = deepcopy(p_vals)
    if method == 'harmonic':
        if w is None:
            w = np.repeat(1 / len(p), len(p))
        p_avg = 1 / np.sum(w / p)
    elif method == 'fisher':
        stat = -2 * np.sum(np.log(p))
        p_avg = 1 - chi2(df=1).cdf(stat)
    return p_avg


# Generates bootstrap indices of a dataset with the option
# to stratify by one of the (binary-valued) variables
def boot_sample(df, by=None, size=None, seed=None, return_df=False):

    # Setting the random states for the samples
    if seed is None:
        seed = np.random.randint(1, 1e6, 1)[0]
    np.random.seed(seed)

    # Getting the sample size
    if size is None:
        size = df.shape[0]

    # Sampling across groups, if group is unspecified
    if by is None:
        np.random.seed(seed)
        idx = range(size)
        boot = np.random.choice(idx, size=size, replace=True)

    # Sampling by group, if group has been specified
    else:
        levels = np.unique(by)
        level_idx = [np.where(by == level)[0] for level in levels]
        boot = np.random.choice(level_idx, size=len(levels), replace=True)
        boot = np.concatenate(boot).ravel()

    if not return_df:
        return boot
    else:
        return df.iloc[boot, :]


def diff_boot_cis(ref,
                  comp,
                  a=0.05,
                  abs_diff=False,
                  method='bca',
                  interpolation='nearest'):
    # Quick check for a valid estimation method
    methods = ['pct', 'diff', 'bca']
    assert method in methods, 'Method must be pct, diff, or bca.'

    # Pulling out the original estiamtes
    ref_stat = pd.Series(ref.cis.stat.drop('true_prev').values)
    ref_scores = ref.scores.drop('true_prev', axis=1)
    comp_stat = pd.Series(comp.cis.stat.drop('true_prev').values)
    comp_scores = comp.scores.drop('true_prev', axis=1)

    # Optionally Reversing the order of comparison
    diff_scores = comp_scores - ref_scores
    diff_stat = comp_stat - ref_stat

    # Setting the quantiles to retrieve
    lower = (a / 2) * 100
    upper = 100 - lower

    # Calculating the percentiles
    if method == 'pct':
        cis = np.nanpercentile(diff_scores,
                               q=(lower, upper),
                               interpolation=interpolation,
                               axis=0)
        cis = pd.DataFrame(cis.transpose())

    elif method == 'diff':
        diffs = diff_stat.values.reshape(1, -1) - diff_scores
        percents = np.nanpercentile(diffs,
                                    q=(lower, upper),
                                    interpolation=interpolation,
                                    axis=0)
        lower_bound = pd.Series(diff_stat + percents[0])
        upper_bound = pd.Series(diff_stat + percents[1])
        cis = pd.concat([lower_bound, upper_bound], axis=1)

    elif method == 'bca':
        # Removing true prevalence from consideration to avoid NaNs
        ref_j_means = ref.jack[1].drop('true_prev')
        ref_j_scores = ref.jack[0].drop('true_prev', axis=1)
        comp_j_means = comp.jack[1].drop('true_prev')
        comp_j_scores = comp.jack[0].drop('true_prev', axis=1)

        # Calculating the bias-correction factor
        n = ref.scores.shape[0]
        stat_vals = diff_stat.transpose().values.ravel()
        n_less = np.sum(diff_scores < stat_vals, axis=0)
        p_less = n_less / n
        z0 = norm.ppf(p_less)

        # Fixing infs in z0
        z0[np.where(np.isinf(z0))[0]] = 0.0

        # Estiamating the acceleration factor
        j_means = comp_j_means - ref_j_means
        j_scores = comp_j_scores - ref_j_scores
        diffs = j_means - j_scores
        numer = np.sum(np.power(diffs, 3))
        denom = 6 * np.power(np.sum(np.power(diffs, 2)), 3 / 2)

        # Getting rid of 0s in the denominator
        zeros = np.where(denom == 0)[0]
        for z in zeros:
            denom[z] += 1e-6

        acc = numer / denom

        # Calculating the bounds for the confidence intervals
        zl = norm.ppf(a / 2)
        zu = norm.ppf(1 - (a / 2))
        lterm = (z0 + zl) / (1 - acc * (z0 + zl))
        uterm = (z0 + zu) / (1 - acc * (z0 + zu))
        lower_q = norm.cdf(z0 + lterm) * 100
        upper_q = norm.cdf(z0 + uterm) * 100

        # Returning the CIs based on the adjusted quantiles
        cis = [
            np.nanpercentile(diff_scores.iloc[:, i],
                             q=(lower_q[i], upper_q[i]),
                             interpolation=interpolation,
                             axis=0) for i in range(len(lower_q))
        ]
        cis = pd.DataFrame(cis, columns=['lower', 'upper'])

    cis = pd.concat([ref_stat, comp_stat, diff_stat, cis], axis=1)
    cis = cis.set_index(ref_scores.columns.values)
    cis.columns = ['ref', 'comp', 'd', 'lower', 'upper']

    return cis


def grid_metrics(targets,
                 guesses,
                 step=.01,
                 min=0.0,
                 max=1.0,
                 by='f1',
                 average='binary',
                 counts=True):
    cutoffs = np.arange(min, max, step)
    if len((guesses.shape)) == 2:
        if guesses.shape[1] == 1:
            guesses = guesses.flatten()
        else:
            guesses = guesses[:, 1]
    if average == 'binary':
        scores = []
        for _, cutoff in enumerate(cutoffs):
            threshed = threshold(guesses, cutoff)
            stats = clf_metrics(targets, threshed)
            stats['cutoff'] = pd.Series(cutoff)
            scores.append(stats)

    return pd.concat(scores, axis=0)


def roc_cis(rocs, alpha=0.05, round=2):
    # Getting the quantiles to make CIs
    lq = (alpha / 2) * 100
    uq = (1 - (alpha / 2)) * 100
    fprs = np.concatenate([roc[0] for roc in rocs], axis=0)
    tprs = np.concatenate([roc[1] for roc in rocs], axis=0)
    roc_arr = np.concatenate(
        [fprs.reshape(-1, 1), tprs.reshape(-1, 1)], axis=1)
    roc_df = pd.DataFrame(roc_arr, columns=['fpr', 'tpr'])
    roc_df.fpr = roc_df.fpr.round(round)
    unique_fprs = roc_df.fpr.unique()
    fpr_idx = [np.where(roc_df.fpr == fpr)[0] for fpr in unique_fprs]
    tpr_quants = [
        np.percentile(roc_df.tpr[idx], q=(lq, 50, uq)) for idx in fpr_idx
    ]
    tpr_quants = np.vstack(tpr_quants)
    quant_arr = np.concatenate([unique_fprs.reshape(-1, 1), tpr_quants],
                               axis=1)
    quant_df = pd.DataFrame(quant_arr,
                            columns=['fpr', 'lower', 'med', 'upper'])
    quant_df = quant_df.sort_values('fpr')
    return quant_df


# Returns the maximum value of metric X that achieves a value of
# at least yval on metric Y
def x_at_y(x, y, yval, grid):
    y = np.array(grid[y])
    x = np.array(grid[x])
    assert np.sum(y >= yval) > 0, 'No y vals meet the minimum'
    good_y = np.where(y >= yval)[0]
    best_x = np.max(x[good_y])
    return best_x


# Converts a boot_cis['cis'] object to a single row
def merge_cis(df, stats, round=4):
    df = deepcopy(df)
    for stat in stats:
        lower = stat + '.lower'
        upper = stat + '.upper'
        new = stat + '.ci'
        l = df[lower].values.round(round)
        u = df[upper].values.round(round)
        strs = [
            pd.Series('(' + str(l[i]) + ', ' + str(u[i]) + ')')
            for i in range(df.shape[0])
        ]
        df[new] = pd.concat(strs, axis=0)
        df = df.drop([lower, upper], axis=1)
    return df


def unique_combo(c):
    if len(np.intersect1d(c[0], c[1])) == 0:
        return c
    else:
        return None


def prop_table(y, pred, axis=0, round=2):
    tab = pd.crosstab(y, pred)
    if axis == 1:
        tab = tab.transpose()
        out = tab / np.sum(tab, axis=0)
        out = out.transpose()
    else:
        out = tab / np.sum(tab, axis=0)
    if round is not None:
        out = np.round(out, round)
    return out


def risk_ratio(y, pred, round=2):
    props = np.array(prop_table(y, pred, round=None))
    rr = props[1, 1] / props[1, 0]
    if round is not None:
        rr = np.round(rr, round)
    return rr


def odds_ratio(y, pred, round=2):
    tab = np.array(pd.crosstab(y, pred))
    OR = (tab[0, 0] * tab[1, 1]) / (tab[1, 0] * tab[0, 1])
    if round is not None:
        OR = np.round(OR, round)
    return OR


def onehot_matrix(y, sparse=False):
    if not sparse:
        y_mat = np.zeros((y.shape[0], len(np.unique(y))))
        for row, col in enumerate(y):
            y_mat[row, col] = 1
    return y_mat


def max_probs(arr, maxes=None, axis=1):
    if maxes is None:
        maxes = np.argmax(arr, axis=axis)
    out = [arr[i, maxes[i]] for i in range(arr.shape[0])]
    return np.array(out)


def write_stats(stats, outcome, stats_dir='output/analysis/'):
    stats_filename = outcome + '_stats.csv'
    if stats_filename in os.listdir(stats_dir):
        stats_df = pd.read_csv(stats_dir + stats_filename)
        stats_df = pd.concat([stats_df, stats], axis=0)
        stats_df.to_csv(stats_dir + stats_filename, index=False)
    else:
        stats.to_csv(stats_dir + stats_filename, index=False)
    return


def write_preds(preds,
                outcome,
                mod_name,
                probs=None,
                test_idx=None,
                cohort_prefix='',
                output_dir='output/',
                stats_folder='analysis/'):
    stats_dir = output_dir + stats_folder
    preds_filename = outcome + '_preds.csv'
    if preds_filename in os.listdir(stats_dir):
        preds_df = pd.read_csv(stats_dir + preds_filename)
    else:
        assert test_idx is not None
        preds_df = pd.read_csv(output_dir + cohort_prefix + 'cohort.csv')
        preds_df = preds_df.iloc[test_idx, :]

    preds_df[mod_name + '_pred'] = preds
    if probs is not None:
        if len(probs.shape) > 1:
            probs = np.max(probs, axis=1)
        preds_df[mod_name + '_prob'] = probs
    preds_df.to_csv(stats_dir + preds_filename, index=False)
    return


# Converts a boot_cis['cis'] object to a single row
def merge_cis(c, round=4, mod_name=''):
    str_cis = c.round(round).astype(str)
    str_paste = pd.DataFrame(str_cis.stat + ' (' + str_cis.lower + ', ' +
                             str_cis.upper + ')',
                             columns=[mod_name]).transpose()
    return str_paste


def merge_ci_list(l, mod_names=None, round=4):
    if type(l[0] != type(pd.DataFrame())):
        l = [c.cis for c in l]
    if mod_names is not None:
        merged_cis = [
            merge_cis(l[i], round, mod_names[i]) for i in range(len(l))
        ]
    else:
        merged_cis = [merge_cis(c, round=round) for c in l]

    return pd.concat(merged_cis, axis=0)
