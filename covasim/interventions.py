import covasim as cv
import pylab as pl
import numpy as np
import sciris as sc

__all__ = ['Intervention', 'ChangeBeta', 'TestNum', 'TestProp']


class Intervention:
    """
    Abstract class for interventions

    """
    def __init__(self):
        self.results = {}  #: All interventions are guaranteed to have results, so `Sim` can safely iterate over this dict

    def apply(self, sim, t: int) -> None:
        """
        Apply intervention

        Function signature matches existing intervention definition
        This method gets called at each timestep and must be implemented
        by derived classes

        Args:
            self:
            sim: The Sim instance
            t: The current time index

        Returns:

        """
        raise NotImplementedError

    def finalize(self, sim):
        """
        Call function at end of simulation

        This can be used to do things like compute cumulative results

        Args:
            sim: the Sim instance

        Returns:

        """
        return

    def to_json(self):
        """
        Return JSON-compatible representation

        Custom classes can't be directly represented in JSON. This method is a
        one-way export to produce a JSON-compatible representation of the
        intervention. In the first instance, the object dict will be returned.
        However, if an intervention itself contains non-standard variables as
        attributes, then its `to_json` method will need to handle those

        Returns: JSON-serializable representation (typically a dict, but could be anything else)

        """
        d = sc.dcp(self.__dict__)
        d['InterventionType'] = self.__class__.__name__
        return d


class ChangeBeta(Intervention):
    '''
    The most basic intervention -- change beta by a certain amount.

    Args:
        days (int or array): the day or array of days to apply the interventions
        changes (float or array): the changes in beta (1 = no change, 0 = no transmission)

    Examples:
        interv = ChangeBeta(25, 0.3) # On day 25, reduce beta by 70% to 0.3
        interv = ChangeBeta([14, 28], [0.7, 1]) # On day 14, reduce beta by 30%, and on day 28, return to 1

    '''

    def __init__(self, days, changes):
        super().__init__()
        self.days = sc.promotetoarray(days)
        self.changes = sc.promotetoarray(changes)
        if len(self.days) != len(self.changes):
            errormsg = f'Number of days supplied ({len(self.days)}) does not match number of changes in beta ({len(self.changes)})'
            raise ValueError(errormsg)
        self.orig_beta = None
        return


    def apply(self, sim, t):

        # If this is the first time it's being run, store beta
        if self.orig_beta is None:
            self.orig_beta = sim['beta']

        # If this day is found in the list, apply the intervention
        inds = sc.findinds(self.days, t)
        if len(inds):
            new_beta = self.orig_beta
            for ind in inds:
                new_beta = new_beta * self.changes[ind]
            sim['beta'] = new_beta

        return


class TestNum(Intervention):
    """
    Test a fixed number of people per day
    """

    def __init__(self, npts, daily_tests, sympt_test=100.0, trace_test=1.0, sensitivity=1.0):
        super().__init__()

        self.daily_tests = daily_tests #: Should be a list of length matching time
        self.sympt_test = sympt_test
        self.trace_test = trace_test
        self.sensitivity = sensitivity

        self.results['n_diagnoses'] = cv.Result('Number diagnosed', npts=npts)
        self.results['cum_diagnoses'] = cv.Result('Cumulative number diagnosed', npts=npts)

        return

    def apply(self, sim, t):

        # Check that there are still tests
        if t < len(self.daily_tests):
            n_tests = self.daily_tests[t]  # Number of tests for this day
        else:
            return

        # If there are no tests today, abort early
        if not (n_tests and pl.isfinite(n_tests)):
            return

        test_probs = np.ones(sim.n)

        for i, person in enumerate(sim.people.values()):
            # Adjust testing probability based on what's happened to the person
            # NB, these need to be separate if statements, because a person can be both diagnosed and infectious/symptomatic
            if person.symptomatic:
                test_probs[i] *= self.sympt_test  # They're symptomatic
            if person.known_contact:
                test_probs[i] *= self.trace_test  # They've had contact with a known positive
            if person.diagnosed:
                test_probs[i] = 0.0

        test_probs /= test_probs.sum()
        test_inds = cv.choose_people_weighted(probs=test_probs, n=n_tests)

        for test_ind in test_inds:
            person = sim.get_person(test_ind)
            person.test(t, self.sensitivity)
            if person.diagnosed:
                self.results['n_diagnoses'][t] += 1

        return


    def finalize(self, sim, *args, **kwargs):
        self.results['cum_diagnoses'].values = pl.cumsum(self.results['n_diagnoses'].values)
        sim.results.update(self.results)
        return


class TestProp(Intervention):
    """
    Test as many people as required based on test probability

    Returns:

    """
    def __init__(self, npts, symptomatic_prob=0.9, asymptomatic_prob=0.01, trace_prob=1.0, test_sensitivity=1.0):
        """

        Args:
            self:
            symptomatic_prob:
            trace_prob:

        Returns:

        """
        super().__init__()
        self.symptomatic_prob = symptomatic_prob
        self.asymptomatic_prob = asymptomatic_prob
        self.trace_prob = trace_prob # Probability that identified contacts get tested
        self.test_sensitivity = test_sensitivity

        # Instantiate the results to track
        self.results['n_tested']      = cv.Result('Number tested', npts=npts)
        self.results['n_diagnoses']   = cv.Result('Number diagnosed', npts=npts)
        self.results['cum_tested']    = cv.Result('Cumulative number tested', npts=npts)
        self.results['cum_diagnoses'] = cv.Result('Cumulative number diagnosed', npts=npts)

        self.scheduled_tests = set() # Track UIDs of people that are guaranteed to be tested at the next step
        return


    def apply(self, sim, t):
        ''' Perform testing '''

        new_scheduled_tests = set()

        for i, person in enumerate(sim.people.values()):
            if i in self.scheduled_tests or (person.symptomatic and cv.bt(self.symptomatic_prob)) or (not person.symptomatic and cv.bt(self.asymptomatic_prob)):
                self.results['n_tested'][t] += 1
                person.test(t, self.test_sensitivity)
                if person.diagnosed:
                    self.results['n_diagnoses'][t] += 1
                    for idx in person.contact_inds:
                        if person.diagnosed and self.trace_prob and cv.bt(self.trace_prob):
                            new_scheduled_tests.add(idx)

        self.scheduled_tests = new_scheduled_tests
        return


    def finalize(self, sim, *args, **kwargs):
        self.results['cum_tested'].values = pl.cumsum(self.results['n_tested'].values)
        self.results['cum_diagnoses'].values = pl.cumsum(self.results['n_diagnoses'].values)
        sim.results.update(self.results)
        return