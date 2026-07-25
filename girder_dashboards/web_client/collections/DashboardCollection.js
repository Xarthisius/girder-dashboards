import DashboardModel from '../models/DashboardModel';

const Collection = girder.collections.Collection;

var DashboardCollection = Collection.extend({
    resourceName: 'dashboard',
    model: DashboardModel,
    sortField: 'name',
    // Dashboards are a handful of curated entries, not a long tail, so fetch
    // them in one page and let the gallery lay them all out.
    pageLimit: 100
});

export default DashboardCollection;
