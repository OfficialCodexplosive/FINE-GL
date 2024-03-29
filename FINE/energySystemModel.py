"""
Last edited: November 12 2020
|br| @author: FINE Developer Team (FZJ IEK-3)
"""

from FINE.component import Component, ComponentModel
from FINE import utils
from tsam.timeseriesaggregation import TimeSeriesAggregation
import pandas as pd
import numpy as np
import pyomo.environ as pyomo
import pyomo.opt as opt
import time
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("always", category=UserWarning)

class EnergySystemModel:
    """
    EnergySystemModel class

    The functionality provided by the EnergySystemModel class is fourfold:\n
    * With it, the **basic structure** (spatial and temporal resolution, considered commodities) of
      the investigated energy system is defined.
    * It serves as a **container for all components** investigated in the energy system model. These components,
      namely sources and sinks, conversion options, storage options, and transmission options
      (in the core module), can be added to an EnergySystemModel instance.
    * It provides the core functionality of **modeling and optimizing the energy system** based on the specified
      structure and components on the one hand and of specified simulation parameters on the other hand.
    * It **stores optimization results** which can then be post-processed with other modules.

    The parameters which are stored in an instance of the class refer to:\n
    * the modeled spatial representation of the energy system (**locations, lengthUnit**)
    * the modeled temporal representation of the energy system (**totalTimeSteps, hoursPerTimeStep,
      years, periods, periodsOrder, periodsOccurrences, timeStepsPerPeriod, interPeriodTimeSteps,
      isTimeSeriesDataClustered, typicalPeriods, tsaInstance, timeUnit**)
    * the considered commodities in the energy system (**commodities, commodityUnitsDict**)
    * the considered components in the energy system (**componentNames, componentModelingDict, costUnit**)
    * optimization related parameters (**pyM, solverSpecs**)\n
    The parameters are first set when a class instance is initiated. The parameters which are related to the
    components (e.g. componentNames) are complemented by adding the components to the class instance.

    Instances of this class provide functions for\n
    * adding components and their respective modeling classes (**add**)
    * clustering the time series data of all added components using the time series aggregation package tsam, cf.
      https://github.com/FZJ-IEK3-VSA/tsam (**cluster**)
    * optimizing the specified energy system (**optimize**), for which a pyomo concrete model instance is built
      and filled with \n
      (0) basic time sets, \n
      (1) sets, variables and constraints contributed by the component modeling classes, \n
      (2) basic, component overreaching constraints, and \n
      (3) an objective function. \n
      The pyomo instance is then optimized by a specified solver. The optimization results are processed once
      available.
    * getting components and their attributes (**getComponent, getCompAttr, getOptimizationSummary**)

    Last edited: November 12 2020
    |br| @author: FINE Developer Team (FZJ IEK-3)
    """

    def __init__(self,
                 locations,
                 commodities,
                 commodityUnitsDict,
                 numberOfTimeSteps=8760,
                 hoursPerTimeStep=1,
                 costUnit='1e9 Euro',
                 lengthUnit='km',
                 verboseLogLevel=0,
                 balanceLimit=None,
                 lowerBound=False):
        """
        Constructor for creating an EnergySystemModel class instance

        **Required arguments:**

        :param locations: locations considered in the energy system
        :type locations: set of strings

        :param commodities: commodities considered in the energy system
        :type commodities: set of strings

        :param commodityUnitsDict: dictionary which assigns each commodity a quantitative unit per time
            (e.g. GW_el, GW_H2, Mio.t_CO2/h). The dictionary is used for results output.
            Note for advanced users: the scale of these units can influence the numerical stability of the
            optimization solver, cf. http://files.gurobi.com/Numerics.pdf where a reasonable range of model
            coefficients is suggested.
        :type commodityUnitsDict: dictionary of strings

        **Default arguments:**

        :param numberOfTimeSteps: number of time steps considered when modeling the energy system (for each
            time step, or each representative time step, variables and constraints are constituted). Together
            with the hoursPerTimeStep, the total number of hours considered can be derived. The total
            number of hours is again used for scaling the arising costs to the arising total annual costs (TAC)
            which are minimized during optimization.
            |br| * the default value is 8760.
        :type totalNumberOfHours: strictly positive integer

        :param hoursPerTimeStep: hours per time step
            |br| * the default value is 1
        :type hoursPerTimeStep: strictly positive float

        :param costUnit: cost unit of all cost related values in the energy system. This argument sets the unit of
            all cost parameters which are given as an input to the EnergySystemModel instance (e.g. for the
            invest per capacity or the cost per operation).
            Note for advanced users: the scale of this unit can influence the numerical stability of the
            optimization solver, cf. http://files.gurobi.com/Numerics.pdf where a reasonable range of model
            coefficients is suggested.
            |br| * the default value is '10^9 Euro' (billion euros), which can be a suitable scale for national
            energy systems.
        :type costUnit: string

        :param lengthUnit: length unit for all length-related values in the energy system.
            Note for advanced users: the scale of this unit can influence the numerical stability of the
            optimization solver, cf. http://files.gurobi.com/Numerics.pdf where a reasonable range of model
            coefficients is suggested.
            |br| * the default value is 'km' (kilometers).
        :type lengthUnit: string

        :param verboseLogLevel: defines how verbose the console logging is:\n
            - 0: general model logging, warnings and optimization solver logging are displayed.
            - 1: warnings are displayed.
            - 2: no general model logging or warnings are displayed, the optimization solver logging is set to a
              minimum.\n
            Note: if required, the optimization solver logging can be separately enabled in the optimizationSpecs
            of the optimize function.
            |br| * the default value is 0
        :type verboseLogLevel: integer (0, 1 or 2)

        :param balanceLimit: defines the balanceLimit constraint (various different balanceLimitIDs possible)
            for specific regions or the whole model. The balancelimitID can be assigned to various components
            of e.g. SourceSinkModel or TransmissionModel to limit the balance of production, consumption and im/export.
            If the balanceLimit is passed as pd.Series it will apply to the overall model, if it is passed
            as pd.Dataframe each column will apply to one region of the multi-node model. In the latter case,
            the number and names of the columns should match the regions/region names in the model.
            Each row contains an individual balanceLimitID as index and the corresponding values for the model
            (pd.Series) or regions (pd.Dataframe). Values are always given in the unit of the esM commodities unit.
            Note: If bounds for sinks shall be specified (e.g. min. export, max. sink volume), values must be
            defined as negative.
            Example: pd.DataFrame(columns=["Region1"], index=["electricity"], data=[1000])
            |br| * the default value is None
        :type balanceLimit: pd.DataFrame or pd.Series

        :param lowerBound: defines whether a lowerBound or an upperBound is considered in the balanceLimitConstraint.
            By default an upperBound is considered. However, multiple cases can be considered:
            1) Sources:
                a) LowerBound=False: UpperBound for commodity from SourceComponent (Define positive value in
                balanceLimit). Example: Limit CO2-Emission
                b) LowerBound=True: LowerBound for commodity from SourceComponent (Define positive value in
                balanceLimit). Example: Require minimum production from renewables.
            2) Sinks:
                a) LowerBound=False: UpperBound in a mathematical sense for commodity from SinkComponent
                (Logically minimum limit for negative values, define negative value in balanceLimit).
                Example: Minimum export/consumption of hydrogen.
                b) LowerBound=True: LowerBound in a mathematical sense for commodity from SourceComponent
                (Logically maximum limit for negative values, define negative value in balanceLimit).
                Example: Define upper limit for Carbon Capture & Storage.
            |br| * the default value is False
        :type lowerBound: bool
        """

        # Check correctness of inputs
        utils.checkEnergySystemModelInput(locations, commodities, commodityUnitsDict, numberOfTimeSteps,
                                          hoursPerTimeStep, costUnit, lengthUnit, balanceLimit)

        ################################################################################################################
        #                                        Spatial resolution parameters                                         #
        ################################################################################################################

        # The locations (set of string) name the considered locations in an energy system model instance. The parameter
        # is used throughout the build of the energy system model to validate inputs and declare relevant sets,
        # variables and constraints.
        # The length unit refers to the measure of length referred throughout the model.
        # The balanceLimit can be used to limit certain balanceLimitIDs defined in the components.
        self.locations, self.lengthUnit = locations, lengthUnit
        self.numberOfTimeSteps = numberOfTimeSteps
        self.balanceLimit = balanceLimit
        self.lowerBound = lowerBound

        ################################################################################################################
        #                                            Time series parameters                                            #
        ################################################################################################################

        # The totalTimeSteps parameter (list, ranging from 0 to the total numberOfTimeSteps-1) refers to the total
        # number of time steps considered when modeling the specified energy system. The parameter is used for
        # validating time series data input and for setting other time series parameters when modeling a full temporal
        # resolution. The hoursPerTimeStep parameter (float > 0) refers to the temporal length of a time step in the
        # totalTimeSteps. From the numberOfTimeSteps and the hoursPerTimeStep the numberOfYears parameter is computed.
        self.totalTimeSteps, self.hoursPerTimeStep = list(range(numberOfTimeSteps)), hoursPerTimeStep
        self.numberOfYears = numberOfTimeSteps * hoursPerTimeStep / 8760.0

        # The periods parameter (list, [0] when considering a full temporal resolution, range of [0, ...,
        # totalNumberOfTimeSteps/numberOfTimeStepsPerPeriod] when applying time series aggregation) represents
        # the periods considered when modeling the energy system. Only one period exists when considering the full
        # temporal resolution. When applying time series aggregation, the full time series are broken down into
        # periods to which a typical period is assigned to.
        # These periods have an order which is stored in the periodsOrder parameter (list, [0] when considering a full
        # temporal resolution, [typicalPeriod(0), ... ,
        # typicalPeriod(totalNumberOfTimeSteps/numberOfTimeStepsPerPeriod-1)] when applying time series aggregation).
        # The occurrences of these periods are stored in the periodsOccurrences parameter (list, [1] when considering a
        # full temporal resolution, [occurrences(0), ..., occurrences(numberOfTypicalPeriods-1)] when applying time
        # series aggregation).
        self.periods, self.periodsOrder, self.periodOccurrences = [0], [0], [1]
        self.timeStepsPerPeriod = list(range(numberOfTimeSteps))
        self.interPeriodTimeSteps = list(range(int(len(self.totalTimeSteps) / len(self.timeStepsPerPeriod)) + 1))

        # The isTimeSeriesDataClustered parameter is used to check data consistency.
        # It is set to True if the class' cluster function is called. It is set to False if a new component is added.
        # If the cluster function is called, the typicalPeriods parameter is set from None to
        # [0, ..., numberOfTypicalPeriods-1] and, if specified, the resulting TimeSeriesAggregation instance is stored
        # in the tsaInstance parameter (default None).
        # The time unit refers to time measure referred throughout the model. Currently, it has to be an hour 'h'.
        self.isTimeSeriesDataClustered, self.typicalPeriods, self.tsaInstance = False, None, None
        self.timeUnit = 'h'

        ################################################################################################################
        #                                        Commodity specific parameters                                         #
        ################################################################################################################

        # The commodities parameter is a set of strings which describes what commodities are considered in the energy
        # system, and hence, which commodity balances need to be considered in the energy system model and its
        # optimization.
        # The commodityUnitsDict parameter is a dictionary which assigns each considered commodity (string) a
        # unit (string) which can be used by results output functions.
        self.commodities = commodities
        self.commodityUnitsDict = commodityUnitsDict

        ################################################################################################################
        #                                        Component specific parameters                                         #
        ################################################################################################################

        # The componentNames parameter is a set of strings in which all in the EnergySystemModel instance considered
        # components are stored. It is used to check that all components have unique indices.
        # The componentModelingDict is a dictionary (modelingClass name: modelingClass instance) in which the in the
        # energy system considered modeling classes are stored (in which again the components modeled with the
        # modelingClass as well as the equations to model them with are stored).
        # The costUnit parameter (string) is the parameter in which all cost input parameter have to be specified.
        self.componentNames = {}
        self.componentModelingDict = {}
        self.costUnit = costUnit

        ################################################################################################################
        #                                           Optimization parameters                                            #
        ################################################################################################################

        # The pyM parameter is None when the EnergySystemModel is initialized. After calling the optimize function,
        # the pyM parameter stores a Concrete Pyomo Model instance which contains parameters, sets, variables,
        # constraints and objective required for the optimization set up and solving.
        # The solverSpecs parameter is a dictionary (string: param) which stores different parameters that are used
        # for solving the optimization problem. The parameters are: solver (string, solver which is used to solve
        # the optimization problem), optimizationSpecs (string, representing **kwargs for the solver), hasTSA (boolean,
        # indicating if time series aggregation is used for the optimization), buildtime (positive float, time needed
        # to declare the optimization problem in seconds), solvetime (positive float, time needed to solve the
        # optimization problem in seconds), runtime (positive float, runtime of the optimization run in seconds),
        # timeLimit (positive float or None, if specified, indicates the maximum allowed runtime of the solver),
        # threads (positive int, number of threads used for optimization, can depend on solver), logFileName
        # (string, name of logfile).
        # The objectiveValue parameter is None when the EnergySystemModel is initialized. After calling the 
        # optimize function, the objective value (i.e. TAC of the analyzed energy system) is stored in the 
        # objectiveValue parameter for easier access.

        self.pyM = None
        self.solverSpecs = {'solver': '', 'optimizationSpecs': '', 'hasTSA': False, 'buildtime': 0, 'solvetime': 0,
                            'runtime': 0, 'timeLimit': None, 'threads': 0, 'logFileName': ''}
        self.objectiveValue = None

        ################################################################################################################
        #                                           General model parameters                                           #
        ################################################################################################################

        # The verbose parameter defines how verbose the console logging is: 0: general model logging, warnings
        # and optimization solver logging are displayed, 1: warnings are displayed, 2: no general model logging or
        # warnings are displayed, the optimization solver logging is set to a minimum.
        # The optimization solver logging can be separately enabled in the optimizationSpecs of the optimize function.
        self.verbose = verboseLogLevel

    def add(self, component):
        """
        Function for adding a component and, if required, its respective modeling class to the EnergySystemModel
        instance. The added component has to inherit from the FINE class Component.

        :param component: the component to be added
        :type component: An object which inherits from the FINE Component class
        """
        if not issubclass(type(component), Component):
            raise TypeError('The added component has to inherit from the FINE class Component.')
        if not issubclass(component.modelingClass, ComponentModel):
            print(component.name, component.modelingClass, ComponentModel)
            raise TypeError('The added component has to inherit from the FINE class ComponentModel.')
        component.addToEnergySystemModel(self)

    def removeComponent(self, componentName, track=False):
        """
        Function which removes a component from the energy system.

        :param componentName: name of the component that should be removed
        :type componentName: string

        :param track: specifies if the removed components should be tracked or not
            |br| * the default value is False
        :type track: boolean

        :returns: dictionary with the removed componentName and component instance if track is set to True else None.
        :rtype: dict or None
        """

        # Test if component exists
        if componentName not in self.componentNames.keys():
            raise ValueError('The component ' + componentName + ' cannot be found in the energy system model.\n' +
                             'The components considered in the model are: ' + str(self.componentNames.keys()))
        modelingClass = self.componentNames[componentName]
        removedComp = dict()
        # If track: Return a dictionary including the name of the removed component and the component instance
        if track:
            removedComp = dict({componentName : self.componentModelingDict[modelingClass].componentsDict.pop(componentName)})
            # Remove component from the componentNames dict:
            del self.componentNames[componentName]
            # Test if all components of one modelingClass are removed. If so, remove modelingClass:
            if not self.componentModelingDict[modelingClass].componentsDict: # False if dict is empty
                del self.componentModelingDict[modelingClass]
            return removedComp
        else:
            # Remove component from the componentNames dict:
            del self.componentNames[componentName]
            # Remove component from the componentModelingDict:
            del self.componentModelingDict[modelingClass].componentsDict[componentName]
            # Test if all components of one modelingClass are removed. If so, remove modelingClass:
            if not self.componentModelingDict[modelingClass].componentsDict: # False if dict is empty
                del self.componentModelingDict[modelingClass]
            return None

    def getComponent(self, componentName):
        """
        Function which returns a component of the energy system.

        :param componentName: name of the component that should be returned
        :type componentName: string

        :returns: the component which has the name componentName
        :rtype: Component
        """
        if componentName not in self.componentNames.keys():
            raise ValueError('The component ' + componentName + ' cannot be found in the energy system model.\n' +
                             'The components considered in the model are: ' + str(self.componentNames.keys()))
        modelingClass = self.componentNames[componentName]
        return self.componentModelingDict[modelingClass].componentsDict[componentName]

    def getComponentAttribute(self, componentName, attributeName):
        """
        Function which returns an attribute of a component considered in the energy system.

        :param componentName: name of the component from which the attribute should be obtained
        :type componentName: string

        :param attributeName: name of the attribute that should be returned
        :type attributeName: string

        :returns: the attribute specified by the attributeName of the component with the name componentName
        :rtype: depends on the specified attribute
        """
        return getattr(self.getComponent(componentName), attributeName)

    def getOptimizationSummary(self, modelingClass, outputLevel=0):
        """
        Function which returns the optimization summary (design variables, aggregated operation variables,
        objective contributions) of a modeling class.

        :param modelingClass: name of the modeling class from which the optimization summary should be obtained
        :type modelingClass: string

        :param outputLevel: states the level of detail of the output summary: \n
            - 0: full optimization summary is returned \n
            - 1: full optimization summary is returned but rows in which all values are NaN (not a number) are dropped\n
            - 2: full optimization summary is returned but rows in which all values are NaN or 0 are dropped \n
            |br| * the default value is 0
        :type outputLevel: integer (0, 1 or 2)

        :returns: the optimization summary of the requested modeling class
        :rtype: pandas DataFrame
        """
        if outputLevel == 0:
            return self.componentModelingDict[modelingClass].optSummary
        elif outputLevel == 1:
            return self.componentModelingDict[modelingClass].optSummary.dropna(how='all')
        else:
            if outputLevel != 2 and self.verbose < 2:
                warnings.warn('Invalid input. An outputLevel parameter of 2 is assumed.')
            df = self.componentModelingDict[modelingClass].optSummary.dropna(how='all')
            return df.loc[((df != 0) & (~df.isnull())).any(axis=1)]

    def cluster(self,
                numberOfTypicalPeriods=7,
                numberOfTimeStepsPerPeriod=24,
                segmentation=False,
                numberOfSegmentsPerPeriod=24,
                clusterMethod='hierarchical',
                sortValues=True,
                storeTSAinstance=False,
                **kwargs):
        """
        Cluster the time series data of all components considered in the EnergySystemModel instance and then
        stores the clustered data in the respective components. For this, the time series data is broken down
        into an ordered sequence of periods (e.g. 365 days) and to each period a typical period (e.g. 7 typical
        days with 24 hours) is assigned. Moreover, the time steps within the periods can further be clustered to bigger
        time steps with an irregular duration using the segmentation option.
        For the clustering itself, the tsam package is used (cf. https://github.com/FZJ-IEK3-VSA/tsam). Additional
        keyword arguments for the TimeSeriesAggregation instance can be added (facilitated by kwargs). As an example: it
        might be useful to add extreme periods to the clustered typical periods.
        Note: The segmentation option can be freely combined with all subclasses. However, an irregular time step length
        is not meaningful for the minimumDownTime and minimumUpTime in the conversionDynamic module, because the time
        would be different for each segment. The same holds true for the DSM module.

        **Default arguments:**

        :param numberOfTypicalPeriods: states the number of typical periods into which the time series data
            should be clustered. The number of time steps per period must be an integer multiple of the total
            number of considered time steps in the energy system.
            Note: Please refer to the tsam package documentation of the parameter noTypicalPeriods for more
            information.
            |br| * the default value is 7
        :type numberOfTypicalPeriods: strictly positive integer

        :param numberOfTimeStepsPerPeriod: states the number of time steps per period
            |br| * the default value is 24
        :type numberOfTimeStepsPerPeriod: strictly positive integer

        :param segmentation: states whether the typical periods should be further segmented to fewer time steps
            |br| * the default value is False
        :type segmentation: boolean

        :param numberOfSegmentsPerPeriod: states the number of segments per period
            |br| * the default value is 24
        :type numberOfSegmentsPerPeriod:  strictly positive integer

        :param clusterMethod: states the method which is used in the tsam package for clustering the time series
            data. Options are for example 'averaging','k_means','exact k_medoid' or 'hierarchical'.
            Note: Please refer to the tsam package documentation of the parameter clusterMethod for more information.
            |br| * the default value is 'hierarchical'
        :type clusterMethod: string

        :param sortValues: states if the algorithm in the tsam package should use
            (a) the sorted duration curves (-> True) or
            (b) the original profiles (-> False)
            of the time series data within a period for clustering.
            Note: Please refer to the tsam package documentation of the parameter sortValues for more information.
            |br| * the default value is True
        :type sortValues: boolean

        :param storeTSAinstance: states if the TimeSeriesAggregation instance created during clustering should be
            stored in the EnergySystemModel instance.
            |br| * the default value is False
        :type storeTSAinstance: boolean

        Last edited: November 12 2020
        |br| @author: FINE Developer Team (FZJ IEK-3)
        """

        # Check input arguments which have to fit the temporal representation of the energy system
        utils.checkClusteringInput(numberOfTypicalPeriods, numberOfTimeStepsPerPeriod, len(self.totalTimeSteps))
        if segmentation:
            if numberOfSegmentsPerPeriod > numberOfTimeStepsPerPeriod:
                if self.verbose < 2:
                    warnings.warn('The chosen number of segments per period exceeds the number of time steps per'
                                  'period. The number of segments per period is set to the number of time steps per '
                                  'period.')
                numberOfSegmentsPerPeriod = numberOfTimeStepsPerPeriod
        hoursPerPeriod = int(numberOfTimeStepsPerPeriod*self.hoursPerTimeStep)

        timeStart = time.time()
        if segmentation:
            utils.output('\nClustering time series data with ' + str(numberOfTypicalPeriods) + ' typical periods and '
                         + str(numberOfTimeStepsPerPeriod) + ' time steps per period \nfurther clustered to '
                         + str(numberOfSegmentsPerPeriod) + ' segments per period...', self.verbose, 0)
        else:
            utils.output('\nClustering time series data with ' + str(numberOfTypicalPeriods) + ' typical periods and '
                         + str(numberOfTimeStepsPerPeriod) + ' time steps per period...', self.verbose, 0)

        # Format data to fit the input requirements of the tsam package:
        # (a) append the time series data from all components stored in all initialized modeling classes to a pandas
        #     DataFrame with unique column names
        # (b) thereby collect the weights which should be considered for each time series as well in a dictionary
        timeSeriesData, weightDict = [], {}
        for mdlName, mdl in self.componentModelingDict.items():
            for compName, comp in mdl.componentsDict.items():
                compTimeSeriesData, compWeightDict = comp.getDataForTimeSeriesAggregation()
                if compTimeSeriesData is not None:
                    timeSeriesData.append(compTimeSeriesData), weightDict.update(compWeightDict)
        timeSeriesData = pd.concat(timeSeriesData, axis=1)
        # Note: Sets index for the time series data. The index is of no further relevance in the energy system model.
        timeSeriesData.index = pd.date_range('2050-01-01 00:30:00', periods=len(self.totalTimeSteps),
                                             freq=(str(self.hoursPerTimeStep) + 'H'), tz='Europe/Berlin')

        # Cluster data with tsam package (the reindex call is here for reproducibility of TimeSeriesAggregation
        # call) depending on whether segmentation is activated or not
        timeSeriesData = timeSeriesData.reindex(sorted(timeSeriesData.columns), axis=1)
        if segmentation:
            clusterClass = TimeSeriesAggregation(timeSeries=timeSeriesData, noTypicalPeriods=numberOfTypicalPeriods,
                                                 segmentation=segmentation, noSegments=numberOfSegmentsPerPeriod,
                                                 hoursPerPeriod=hoursPerPeriod,
                                                 clusterMethod=clusterMethod, sortValues=sortValues,
                                                 weightDict=weightDict, **kwargs)
            # Convert the clustered data to a pandas DataFrame with the first index as typical period number and the
            # second index as segment number per typical period.
            data = pd.DataFrame.from_dict(clusterClass.clusterPeriodDict).reset_index(level=2, drop=True)
            # Get the length of each segment in each typical period with the first index as typical period number and
            # the second index as segment number per typical period.
            timeStepsPerSegment = pd.DataFrame.from_dict(clusterClass.segmentDurationDict)['Segment Duration']
        else:
            clusterClass = TimeSeriesAggregation(timeSeries=timeSeriesData, noTypicalPeriods=numberOfTypicalPeriods,
                                                 hoursPerPeriod=hoursPerPeriod,
                                                 clusterMethod=clusterMethod, sortValues=sortValues,
                                                 weightDict=weightDict, **kwargs)
            # Convert the clustered data to a pandas DataFrame with the first index as typical period number and the
            # second index as time step number per typical period.
            data = pd.DataFrame.from_dict(clusterClass.clusterPeriodDict)
        # Store the respective clustered time series data in the associated components
        for mdlName, mdl in self.componentModelingDict.items():
            for compName, comp in mdl.componentsDict.items():
                comp.setAggregatedTimeSeriesData(data)

        # Store time series aggregation parameters in class instance
        if storeTSAinstance:
            self.tsaInstance = clusterClass
        self.typicalPeriods = clusterClass.clusterPeriodIdx
        self.timeStepsPerPeriod = list(range(numberOfTimeStepsPerPeriod))
        self.segmentation = segmentation
        if segmentation:
            self.segmentsPerPeriod = list(range(numberOfSegmentsPerPeriod))
            self.timeStepsPerSegment = timeStepsPerSegment
            self.hoursPerSegment = self.hoursPerTimeStep * self.timeStepsPerSegment
            # Define start time hour of each segment in each typical period
            segmentStartTime = self.hoursPerSegment.groupby(level=0).cumsum()
            segmentStartTime.index = segmentStartTime.index.set_levels(segmentStartTime.index.levels[1] + 1, level=1)
            lvl0, lvl1 = segmentStartTime.index.levels
            segmentStartTime = segmentStartTime.reindex(pd.MultiIndex.from_product([lvl0, [0, *lvl1]]))
            segmentStartTime[segmentStartTime.index.get_level_values(1) == 0] = 0
            self.segmentStartTime = segmentStartTime
        self.periods = list(range(int(len(self.totalTimeSteps) / len(self.timeStepsPerPeriod))))
        self.interPeriodTimeSteps = list(range(int(len(self.totalTimeSteps) / len(self.timeStepsPerPeriod)) + 1))
        self.periodsOrder = clusterClass.clusterOrder
        self.periodOccurrences = [(self.periodsOrder == tp).sum() for tp in self.typicalPeriods]

        # Set cluster flag to true (used to ensure consistently clustered time series data)
        self.isTimeSeriesDataClustered = True
        utils.output("\t\t(%.4f" % (time.time() - timeStart) + " sec)\n", self.verbose, 0)

    def declareTimeSets(self, pyM, timeSeriesAggregation, segmentation):
        """
        Set and initialize basic time parameters and sets.

        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel

        :param timeSeriesAggregation: states if the optimization of the energy system model should be done with
            (a) the full time series (False) or
            (b) clustered time series data (True).
            |br| * the default value is False
        :type timeSeriesAggregation: boolean

        :param segmentation: states if the optimization of the energy system model based on clustered time series data
            should be done with
            (a) aggregated typical periods with the original time step length (False) or
            (b) aggregated typical periods with further segmented time steps (True).
            |br| * the default value is False
        :type segmentation: boolean
        """

        # Store the information if aggregated time series data is considered for modeling the energy system in the pyomo
        # model instance and set the time series which is again considered for modeling in all components accordingly
        pyM.hasTSA = timeSeriesAggregation
        pyM.hasSegmentation = segmentation
        for mdl in self.componentModelingDict.values():
            for comp in mdl.componentsDict.values():
                comp.setTimeSeriesData(pyM.hasTSA)

        # Set the time set and the inter time steps set. The time set is a set of tuples. A tuple consists of two
        # entries, the first one indicates an index of a period and the second one indicates a time step inside that
        # period. If time series aggregation is not considered, only one period (period 0) exists and the time steps
        # range from 0 up until the specified number of total time steps - 1. Otherwise, the time set is initialized for
        # each typical period (0 ... numberOfTypicalPeriods-1) and the number of time steps per period (0 ...
        # numberOfTimeStepsPerPeriod-1).
        # The inter time steps set is a set of tuples as well, which again consist of two values. The first value again
        # indicates the period, however, the second one now refers to a point in time right before or after a time step
        # (or between two time steps). Hence, the second value reaches values from (0 ... numberOfTimeStepsPerPeriod).
        if not pyM.hasTSA:
            # Reset timeStepsPerPeriod in case it was overwritten by the clustering function
            self.timeStepsPerPeriod = self.totalTimeSteps
            self.interPeriodTimeSteps = list(range(int(len(self.totalTimeSteps) /
                                                        len(self.timeStepsPerPeriod)) + 1))
            self.periods = [0]
            self.periodsOrder = [0]
            self.periodOccurrences = [1]

            # Define sets
            def initTimeSet(pyM):
                return ((p, t) for p in self.periods for t in self.timeStepsPerPeriod)

            def initInterTimeStepsSet(pyM):
                return ((p, t) for p in self.periods for t in range(len(self.timeStepsPerPeriod) + 1))
        else:
            if not pyM.hasSegmentation:
                utils.output('Time series aggregation specifications:\n'
                             'Number of typical periods:' + str(len(self.typicalPeriods)) +
                             ', number of time steps per period:' + str(len(self.timeStepsPerPeriod)) + '\n',
                             self.verbose, 0)

                # Define sets
                def initTimeSet(pyM):
                    return ((p, t) for p in self.typicalPeriods for t in self.timeStepsPerPeriod)

                def initInterTimeStepsSet(pyM):
                    return ((p, t) for p in self.typicalPeriods for t in range(len(self.timeStepsPerPeriod) + 1))
            else:
                utils.output('Time series aggregation specifications:\n'
                             'Number of typical periods:' + str(len(self.typicalPeriods)) +
                             ', number of time steps per period:' + str(len(self.timeStepsPerPeriod)) +
                             ', number of segments per period:' + str(len(self.segmentsPerPeriod)) + '\n',
                             self.verbose, 0)

                # Define sets
                def initTimeSet(pyM):
                    return ((p, t) for p in self.typicalPeriods for t in self.segmentsPerPeriod)

                def initInterTimeStepsSet(pyM):
                    return ((p, t) for p in self.typicalPeriods for t in range(len(self.segmentsPerPeriod) + 1))

        # Initialize sets
        pyM.timeSet = pyomo.Set(dimen=2, initialize=initTimeSet)
        pyM.interTimeStepsSet = pyomo.Set(dimen=2, initialize=initInterTimeStepsSet)

    def declareBalanceLimitConstraint(self, pyM, timeSeriesAggregation):
        """
        Declare balance limit constraint.

        Balance limit constraint can limit the exchange of commodities within the model or over the model region
        boundaries. See the documentation of the parameters for further explanation. In general the following equation
        applies:
            E_source - E_sink + E_exchange,in - E_exchange,out <= E_lim (self.LowerBound=False)
            E_source - E_sink + E_exchange,in - E_exchange,out >= E_lim (self.LowerBound=True)

        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel

        :param timeSeriesAggregation: states if the optimization of the energy system model should be done with
            (a) the full time series (False) or
            (b) clustered time series data (True).
            |br| * the default value is False
        :type timeSeriesAggregation: boolean
        """
        balanceLimitDict = {}
        # 2 differentiations (or 4 cases). 1st: Locational or not; 2nd: lowerBound or not (lower bound)
        # DataFrame with locational input. Otherwise error is thrown in input check.
        if type(self.balanceLimit) == pd.DataFrame:
            for mdl_type, mdl in self.componentModelingDict.items():
                if mdl_type=="SourceSinkModel" or mdl_type=="TransmissionModel":
                    for compName, comp in mdl.componentsDict.items():
                        if comp.balanceLimitID is not None:
                            [balanceLimitDict.setdefault((comp.balanceLimitID, loc), []).append(compName)
                             for loc in self.locations]
            setattr(pyM, "balanceLimitDict", balanceLimitDict)

            def balanceLimitConstraint(pyM, ID, loc):
                # Check whether we want to consider an upper or lower bound.
                if not self.lowerBound:
                    return sum(mdl.getBalanceLimitContribution(esM=self, pyM=pyM, ID=ID,
                                                               timeSeriesAggregation=timeSeriesAggregation, loc=loc)
                               for mdl_type, mdl in self.componentModelingDict.items() if (
                            mdl_type=="SourceSinkModel" or mdl_type=="TransmissionModel")
                               ) <= self.balanceLimit.loc[ID, loc]
                else:
                    return sum(mdl.getBalanceLimitContribution(esM=self, pyM=pyM, ID=ID,
                                                               timeSeriesAggregation=timeSeriesAggregation, loc=loc)
                               for mdl_type, mdl in self.componentModelingDict.items() if (
                            mdl_type=="SourceSinkModel" or mdl_type=="TransmissionModel")
                               ) >= self.balanceLimit.loc[ID, loc]
        # Series as input. Whole model is considered.
        else:
            for mdl_type, mdl in self.componentModelingDict.items():
                if mdl_type=="SourceSinkModel":
                    for compName, comp in mdl.componentsDict.items():
                        if comp.balanceLimitID is not None:
                            balanceLimitDict.setdefault((comp.balanceLimitID), []).append(compName)
            setattr(pyM, "balanceLimitDict", balanceLimitDict)

            def balanceLimitConstraint(pyM, ID):
                # Check wether we want to consider an upper or lower bound
                if not self.lowerBound:
                    return sum(mdl.getBalanceLimitContribution(esM=self, pyM=pyM, ID=ID,
                                                               timeSeriesAggregation=timeSeriesAggregation)
                               for mdl_type, mdl in self.componentModelingDict.items() if (
                            mdl_type=="SourceSinkModel")) <= self.balanceLimit.loc[ID]
                else:
                    return sum(mdl.getBalanceLimitContribution(esM=self, pyM=pyM, ID=ID,
                                                               timeSeriesAggregation=timeSeriesAggregation)
                               for mdl_type, mdl in self.componentModelingDict.items() if (
                                       mdl_type == "SourceSinkModel")) >= self.balanceLimit.loc[ID]
        pyM.balanceLimitConstraint = \
            pyomo.Constraint(pyM.balanceLimitDict.keys(), rule=balanceLimitConstraint)

    def declareSharedPotentialConstraints(self, pyM):
        """
        Declare shared potential constraints, e.g. if a maximum potential of salt caverns has to be shared by
        salt cavern storing methane and salt caverns storing hydrogen.

        .. math:: 
            
            \\underset{\\text{comp} \in \mathcal{C}^{ID}}{\sum} \\text{cap}^{comp}_{loc} / \\text{capMax}^{comp}_{loc} \leq 1 


        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel
        
        """
        utils.output('Declaring shared potential constraint...', self.verbose, 0)

        # Create shared potential dictionary (maps a shared potential ID and a location to components who share the
        # potential)
        potentialDict = {}
        for mdl in self.componentModelingDict.values():
            for compName, comp in mdl.componentsDict.items():
                if comp.sharedPotentialID is not None:
                    [potentialDict.setdefault((comp.sharedPotentialID, loc), []).append(compName)
                     for loc in comp.locationalEligibility.index if comp.capacityMax[loc] != 0]
        pyM.sharedPotentialDict = potentialDict

        # Define and initialize constraints for each instance and location where components have to share an available
        # potential. Sum up the relative contributions to the shared potential and ensure that the total share is
        # <= 100%. For this, get the contributions to the shared potential for the corresponding ID and
        # location from each modeling class.
        def sharedPotentialConstraint(pyM, ID, loc):
            return sum(mdl.getSharedPotentialContribution(pyM, ID, loc)
                       for mdl in self.componentModelingDict.values()) <= 1
        pyM.ConstraintSharedPotentials = \
            pyomo.Constraint(pyM.sharedPotentialDict.keys(), rule=sharedPotentialConstraint)
    
    def declareComponentLinkedQuantityConstraints(self, pyM):
        """
        Declare linked component quantity constraint, e.g. if an engine (E-Motor) is built also a storage (Battery)
        and a vehicle body (e.g. BEV Car) needs to be built. Not the capacity of the components, but the number of
        the components is linked.

        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel     
        """
        utils.output('Declaring linked component quantity constraint...', self.verbose, 0)

        compDict = {}
        for mdl in self.componentModelingDict.values():
            for compName, comp in mdl.componentsDict.items():
                if comp.linkedQuantityID is not None:
                    [compDict.setdefault((comp.linkedQuantityID, loc), []).append(compName)
                    for loc in comp.locationalEligibility.index]
        pyM.linkedQuantityDict = compDict
       
        def linkedQuantityConstraint(pyM, ID, loc, compName1, compName2):
            abbrvName1 = self.componentModelingDict[self.componentNames[compName1]].abbrvName
            abbrvName2 = self.componentModelingDict[self.componentNames[compName2]].abbrvName
            capVar1 = getattr(pyM, 'cap_' + abbrvName1)
            capVar2 = getattr(pyM, 'cap_' + abbrvName2)
            capPPU1 = self.componentModelingDict[self.componentNames[compName1]].componentsDict[compName1].capacityPerPlantUnit
            capPPU2 = self.componentModelingDict[self.componentNames[compName2]].componentsDict[compName2].capacityPerPlantUnit
            return capVar1[loc, compName1] / capPPU1 == capVar2[loc, compName2] / capPPU2


        for (i,j) in pyM.linkedQuantityDict.keys():
            linkedQuantityList = []
            linkedQuantityList.append((i, j))
            
            setattr(pyM, 'ConstraintLinkedQuantity_' + str(i) + '_' + str(j),\
                pyomo.Constraint(\
                    linkedQuantityList,\
                    pyM.linkedQuantityDict[i, j],\
                    pyM.linkedQuantityDict[i, j],\
                    rule=linkedQuantityConstraint))

    def declareCommodityBalanceConstraints(self, pyM):
        """
        Declare commodity balance constraints (one balance constraint for each commodity, location and time step)

        .. math:: 
            
            \\underset{\\text{comp} \in \mathcal{C}^{comm}_{loc}}{\sum} \\text{C}^{comp,comm}_{loc,p,t} = 0 

        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel
        """
        utils.output('Declaring commodity balances...', self.verbose, 0)

        # Declare and initialize a set that states for which location and commodity the commodity balance constraints
        # are non-trivial (i.e. not 0 == 0; trivial constraints raise errors in pyomo).
        def initLocationCommoditySet(pyM):
            return ((loc, commod) for loc in self.locations for commod in self.commodities
                    if any([mdl.hasOpVariablesForLocationCommodity(self, loc, commod)
                            for mdl in self.componentModelingDict.values()]))
        pyM.locationCommoditySet = pyomo.Set(dimen=2, initialize=initLocationCommoditySet)

        # Declare and initialize commodity balance constraints by checking for each location and commodity in the
        # locationCommoditySet and for each period and time step within the period if the commodity source and sink
        # terms add up to zero. For this, get the contribution to commodity balance from each modeling class.
        def commodityBalanceConstraint(pyM, loc, commod, p, t):
            return sum(mdl.getCommodityBalanceContribution(pyM, commod, loc, p, t)
                       for mdl in self.componentModelingDict.values()) == 0
        pyM.commodityBalanceConstraint = pyomo.Constraint(pyM.locationCommoditySet, pyM.timeSet,
                                                          rule=commodityBalanceConstraint)

    def declareObjective(self, pyM):
        """
        Declare the objective function by obtaining the contributions to the objective function from all modeling
        classes. Currently, the only objective function which can be selected is the sum of the total annual cost of all
        components.
        
        .. math::
            z^* = \\min \\underset{comp \\in \\mathcal{C}}{\\sum} \\ \\underset{loc \\in \\mathcal{L}^{comp}}{\\sum} 
            \\left( TAC_{loc}^{comp,cap}  +  TAC_{loc}^{comp,bin} + TAC_{loc}^{comp,op} \\right)

        Objective Function detailed:

        .. math::
            :nowrap:

            \\begin{eqnarray*}
            z^* = \\min & & \\underset{comp \\in \\mathcal{C}}{\\sum}  \\ \\underset{loc \\in \\mathcal{L}^{comp}}{\\sum}
            \\left[ \\text{F}^{comp,cap}_{loc} \\cdot \\left(  \\frac{\\text{investPerCap}^{comp}_{loc}}{\\text{CCF}^{comp}_{loc}} \\right.
            + \\text{opexPerCap}^{comp}_{loc} \\right) \\cdot cap^{comp}_{loc} \\\\
            & & + \\ \\text{F}^{comp,bin}_{loc} \\cdot \\left( \\frac{\\text{investIfBuilt}^{comp}_{loc}}	{CCF^{comp}_{loc}} 
            + \\text{opexIfBuilt}^{comp}_{loc} \\right)  \\cdot  bin^{comp}_{loc} \\\\
            & & \\left. + \\left( \\underset{(p,t) \\in \\mathcal{P} \\times \\mathcal{T}}{\\sum} \\ \\underset{\\text{opType} \\in \\mathcal{O}^{comp}}{\\sum} \\text{factorPerOp}^{comp,opType}_{loc} \\cdot op^{comp,opType}_{loc,p,t} \\cdot  \\frac{\\text{freq(p)}}{\\tau^{years}} \\right) \\right]
            \\end{eqnarray*}
        
        :param pyM: a pyomo ConcreteModel instance which contains parameters, sets, variables,
            constraints and objective required for the optimization set up and solving.
        :type pyM: pyomo ConcreteModel
        """
        utils.output('Declaring objective function...', self.verbose, 0)

        def objective(pyM):
            TAC = sum(mdl.getObjectiveFunctionContribution(self, pyM) for mdl in self.componentModelingDict.values())
            return TAC
        pyM.Obj = pyomo.Objective(rule=objective)

    def declareOptimizationProblem(self, timeSeriesAggregation=False, segmentation=False, relaxIsBuiltBinary=False):
        """
        Declare the optimization problem belonging to the specified energy system for which a pyomo concrete model
        instance is built and filled with
        * basic time sets,
        * sets, variables and constraints contributed by the component modeling classes,
        * basic, component overreaching constraints, and
        * an objective function.

        **Default arguments:**

        :param timeSeriesAggregation: states if the optimization of the energy system model should be done with
            (a) the full time series (False) or
            (b) clustered time series data (True).
            |br| * the default value is False
        :type timeSeriesAggregation: boolean

        :param segmentation: states if the optimization of the energy system model based on clustered time series data
            should be done with
            (a) aggregated typical periods with the original time step length (False) or
            (b) aggregated typical periods with further segmented time steps (True).
            |br| * the default value is False
        :type segmentation: boolean

        :param relaxIsBuiltBinary: states if the optimization problem should be solved as a relaxed LP to get the lower
            bound of the problem.
            |br| * the default value is False
        :type declaresOptimizationProblem: boolean

        Last edited: March 26, 2020
        |br| @author: FINE Developer Team (FZJ IEK-3)
        """
        # Get starting time of the optimization to, later on, obtain the total run time of the optimize function call
        timeStart = time.time()

        # Check correctness of inputs
        utils.checkDeclareOptimizationProblemInput(timeSeriesAggregation, self.isTimeSeriesDataClustered)

        ################################################################################################################
        #                           Initialize mathematical model (ConcreteModel) instance                             #
        ################################################################################################################

        # Initialize a pyomo ConcreteModel which will be used to store the mathematical formulation of the model.
        # The ConcreteModel instance is stored in the EnergySystemModel instance, which makes it available for
        # post-processing or debugging. A pyomo Suffix with the name dual is declared to make dual values associated
        # to the model's constraints available after optimization.
        self.pyM = pyomo.ConcreteModel()
        pyM = self.pyM
        pyM.dual = pyomo.Suffix(direction=pyomo.Suffix.IMPORT)

        # Set time sets for the model instance
        self.declareTimeSets(pyM, timeSeriesAggregation, segmentation)

        ################################################################################################################
        #                         Declare component specific sets, variables and constraints                           #
        ################################################################################################################

        for key, mdl in self.componentModelingDict.items():
            _t = time.time()
            utils.output('Declaring sets, variables and constraints for ' + key, self.verbose, 0)
            utils.output('\tdeclaring sets... ', self.verbose, 0), mdl.declareSets(self, pyM)
            utils.output('\tdeclaring variables... ', self.verbose, 0), mdl.declareVariables(self, pyM, relaxIsBuiltBinary)
            utils.output('\tdeclaring constraints... ', self.verbose, 0), mdl.declareComponentConstraints(self, pyM)
            utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        ################################################################################################################
        #                              Declare cross-componential sets and constraints                                 #
        ################################################################################################################

        # Declare constraints for enforcing shared capacities
        _t = time.time()
        self.declareSharedPotentialConstraints(pyM)
        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        # Declare constraints for linked quantities
        _t = time.time()
        self.declareComponentLinkedQuantityConstraints(pyM)
        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        # Declare commodity balance constraints (one balance constraint for each commodity, location and time step)
        _t = time.time()
        self.declareCommodityBalanceConstraints(pyM)
        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        # Declare constraint for balanceLimit
        _t = time.time()
        self.declareBalanceLimitConstraint(pyM, timeSeriesAggregation)
        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        ################################################################################################################
        #                                         Declare objective function                                           #
        ################################################################################################################

        # Declare objective function by obtaining the contributions to the objective function from all modeling classes
        _t = time.time()
        self.declareObjective(pyM)
        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        # Store the build time of the optimize function call in the EnergySystemModel instance
        self.solverSpecs['buildtime'] = time.time() - timeStart

    def optimize(self,
                 declaresOptimizationProblem=True,
                 relaxIsBuiltBinary=False,
                 timeSeriesAggregation=False,
                 logFileName='', 
                 threads=3, 
                 solver='None', 
                 timeLimit=None, 
                 optimizationSpecs='',
                 warmstart=False):
        """
        Optimize the specified energy system for which a pyomo ConcreteModel instance is built or called upon.
        A pyomo instance is optimized with the specified inputs, and the optimization results are further
        processed.

        **Default arguments:**

        :param declaresOptimizationProblem: states if the optimization problem should be declared (True) or not (False).
            (a) If true, the declareOptimizationProblem function is called and a pyomo ConcreteModel instance is built.
            (b) If false a previously declared pyomo ConcreteModel instance is used.
            |br| * the default value is True
        :type declaresOptimizationProblem: boolean

        :param relaxIsBuiltBinary: states if the optimization problem should be solved as a relaxed LP to get the lower
            bound of the problem.
            |br| * the default value is False
        :type declaresOptimizationProblem: boolean

        :param timeSeriesAggregation: states if the optimization of the energy system model should be done with
            (a) the full time series (False) or
            (b) clustered time series data (True).
            |br| * the default value is False
        :type timeSeriesAggregation: boolean

        :param segmentation: states if the optimization of the energy system model based on clustered time series data
            should be done with
            (a) aggregated typical periods with the original time step length (False) or
            (b) aggregated typical periods with further segmented time steps (True).
            |br| * the default value is False
        :type segmentation: boolean

        :param logFileName: logFileName is used for naming the log file of the optimization solver output
            if gurobi is used as the optimization solver.
            If the logFileName is given as an absolute path (e.g. logFileName = os.path.join(os.getcwd(),
            'Results', 'logFileName.txt')) the log file will be stored in the specified directory. Otherwise,
            it will be stored by default in the directory where the executing python script is called.
            |br| * the default value is 'job'
        :type logFileName: string

        :param threads: number of computational threads used for solving the optimization (solver dependent
            input) if gurobi is used as the solver. A value of 0 results in using all available threads. If
            a value larger than the available number of threads are chosen, the value will reset to the maximum
            number of threads.
            |br| * the default value is 3
        :type threads: positive integer

        :param solver: specifies which solver should solve the optimization problem (which of course has to be
            installed on the machine on which the model is run).
            |br| * the default value is 'gurobi'
        :type solver: string

        :param timeLimit: if not specified as None, indicates the maximum solve time of the optimization problem
            in seconds (solver dependent input). The use of this parameter is suggested when running models in
            runtime restricted environments (such as clusters with job submission systems). If the runtime
            limitation is triggered before an optimal solution is available, the best solution obtained up
            until then (if available) is processed.
            |br| * the default value is None
        :type timeLimit: strictly positive integer or None

        :param optimizationSpecs: specifies parameters for the optimization solver (see the respective solver
            documentation for more information). Example: 'LogToConsole=1 OptimalityTol=1e-6'
            |br| * the default value is an empty string ('')
        :type timeLimit: string

        :param warmstart: specifies if a warm start of the optimization should be considered
            (not always supported by the solvers).
            |br| * the default value is False
        :type warmstart: boolean

        Last edited: March 26, 2020
        |br| @author: FINE Developer Team (FZJ IEK-3)
        """

        if not timeSeriesAggregation:
            self.segmentation = False

        if declaresOptimizationProblem:
            self.declareOptimizationProblem(timeSeriesAggregation=timeSeriesAggregation, segmentation=self.segmentation,
                                            relaxIsBuiltBinary=relaxIsBuiltBinary)
        else:
            if self.pyM is None:
                raise TypeError('The optimization problem is not declared yet. Set the argument declaresOptimization'
                                ' problem to True or call the declareOptimizationProblem function first.')

        # Get starting time of the optimization to, later on, obtain the total run time of the optimize function call
        timeStart = time.time()

        # Check correctness of inputs
        utils.checkOptimizeInput(timeSeriesAggregation, self.isTimeSeriesDataClustered, logFileName, threads, solver,
                                 timeLimit, optimizationSpecs, warmstart)

        # Store keyword arguments in the EnergySystemModel instance
        self.solverSpecs['logFileName'], self.solverSpecs['threads'] = logFileName, threads
        self.solverSpecs['solver'], self.solverSpecs['timeLimit'] = solver, timeLimit
        self.solverSpecs['optimizationSpecs'], self.solverSpecs['hasTSA'] = optimizationSpecs, timeSeriesAggregation

        # Check which solvers are available and choose default solver if no solver is specified explicitely
        # Order of possible solvers in solverList defines the priority of chosen default solver.
        solverList = ['gurobi', 'coincbc', 'glpk']
        
        if solver != 'None':
            try:
                opt.SolverFactory(solver).available()
            except:
                solver = 'None'

        if solver == 'None':
            for nSolver in solverList:
                if solver == 'None':
                    try:
                        if opt.SolverFactory(nSolver).available():
                            solver = nSolver
                            utils.output('Either solver not selected or specified solver not available.' + str(nSolver) + ' is set as solver.', self.verbose, 0)
                    except:
                        pass

        if solver == 'None':
            raise TypeError('At least one solver must be installed.'
                            ' Have a look at the FINE documentation to see how to install possible solvers.'
                            ' https://vsa-fine.readthedocs.io/en/latest/')

        ################################################################################################################
        #                                  Solve the specified optimization problem                                    #
        ################################################################################################################

        # Set which solver should solve the specified optimization problem
        optimizer = opt.SolverFactory(solver)

        # Set, if specified, the time limit
        if self.solverSpecs['timeLimit'] is not None and solver == 'gurobi':
            optimizer.options['timelimit'] = timeLimit

        # Set the specified solver options
        if 'LogToConsole=' not in optimizationSpecs and solver == "gurobi":
            if self.verbose == 2:
                optimizationSpecs += ' LogToConsole=0'

        # Solve optimization problem. The optimization solve time is stored and the solver information is printed.
        if solver=='gurobi':
            optimizer.set_options('Threads=' + str(threads) + ' logfile=' + logFileName + ' ' + optimizationSpecs)
            solver_info = optimizer.solve(self.pyM, warmstart=warmstart, tee=True)
        elif solver=="glpk":
            optimizer.set_options(optimizationSpecs)
            solver_info = optimizer.solve(self.pyM, tee=True)
        else:
            solver_info = optimizer.solve(self.pyM, tee=True)
        self.solverSpecs['solvetime'] = time.time() - timeStart
        utils.output(solver_info.solver(), self.verbose, 0), utils.output(solver_info.problem(), self.verbose, 0)
        utils.output('Solve time: ' + str(self.solverSpecs['solvetime']) + ' sec.', self.verbose, 0)

        ################################################################################################################
        #                                      Post-process optimization output                                        #
        ################################################################################################################

        _t = time.time()

        # Post-process the optimization output by differentiating between different solver statuses and termination
        # conditions. First, check if the status and termination_condition of the optimization are acceptable.
        # If not, no output is generated.
        # TODO check if this is still compatible with the latest pyomo version
        status, termCondition = solver_info.solver.status, solver_info.solver.termination_condition
        self.solverSpecs['status'] = str(status)
        self.solverSpecs['terminationCondition'] = str(termCondition)
        if status == opt.SolverStatus.error or status == opt.SolverStatus.aborted or status == opt.SolverStatus.unknown:
            utils.output('Solver status:  ' + str(status) + ', termination condition:  ' + str(termCondition) +
                         '. No output is generated.', self.verbose, 0)
        elif solver_info.solver.termination_condition == opt.TerminationCondition.infeasibleOrUnbounded or \
            solver_info.solver.termination_condition == opt.TerminationCondition.infeasible or \
                solver_info.solver.termination_condition == opt.TerminationCondition.unbounded:
                utils.output('Optimization problem is ' + str(solver_info.solver.termination_condition) +
                             '. No output is generated.', self.verbose, 0)
        else:
            # If the solver status is not okay (hence either has a warning, an error, was aborted or has an unknown
            # status), show a warning message.
            if not solver_info.solver.termination_condition == opt.TerminationCondition.optimal and self.verbose < 2:
                warnings.warn('Output is generated for a non-optimal solution.')
            utils.output("\nProcessing optimization output...", self.verbose, 0)
            # Declare component specific sets, variables and constraints
            w = str(len(max(self.componentModelingDict.keys()))+6)
            for key, mdl in self.componentModelingDict.items():
                __t = time.time()
                mdl.setOptimalValues(self, self.pyM)
                outputString = ('for {:' + w + '}').format(key + ' ...') + "(%.4f" % (time.time() - __t) + "sec)"
                utils.output(outputString, self.verbose, 0)
            # Store the objective value in the EnergySystemModel instance.
            self.objectiveValue = self.pyM.Obj()

        utils.output('\t\t(%.4f' % (time.time() - _t) + ' sec)\n', self.verbose, 0)

        # Store the runtime of the optimize function call in the EnergySystemModel instance
        self.solverSpecs['runtime'] = self.solverSpecs['buildtime'] + time.time() - timeStart
