from urllib import urlencode
import logging

from paste.deploy.converters import asbool
from pylons import config

import ckan.model as model
import ckan.lib.base as base
import ckan.lib.helpers as h
import ckan.lib.navl.dictization_functions as dict_fns
import ckan.logic as logic
import ckan.plugins as p
from ckan.common import OrderedDict, c, request, _, g
from ckan.controllers.package import PackageController, search_url, _encode_params

from ckanext.showcase.model import ShowcasePackageAssociation
from ckanext.showcase.plugin import DATASET_TYPE_NAME

render = base.render
abort = base.abort
redirect = base.redirect
NotFound = logic.NotFound
ValidationError = logic.ValidationError
check_access = logic.check_access
get_action = logic.get_action
tuplize_dict = logic.tuplize_dict
clean_dict = logic.clean_dict
parse_params = logic.parse_params
NotAuthorized = logic.NotAuthorized


log = logging.getLogger(__name__)


class ShowcaseController(PackageController):

    def new(self, data=None, errors=None, error_summary=None):

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'auth_user_obj': c.userobj,
                   'save': 'save' in request.params}

        # Check access here, then continue with PackageController.new()
        # PackageController.new will also check access for package_create.
        # This is okay for now, while only sysadmins can create Showcases, but
        # may not work if we allow other users to create Showcases, who don't
        # have access to create dataset package types. Same for edit below.
        try:
            check_access('ckanext_showcase_create', context)
        except NotAuthorized:
            abort(401, _('Unauthorized to create a package'))

        return super(ShowcaseController, self).new(data=data, errors=errors,
                                                   error_summary=error_summary)

    def edit(self, id, data=None, errors=None, error_summary=None):
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'auth_user_obj': c.userobj,
                   'save': 'save' in request.params,
                   'moderated': config.get('moderated'),
                   'pending': True}

        try:
            check_access('ckanext_showcase_update', context)
        except NotAuthorized:
            abort(401, _('User %r not authorized to edit %s') % (c.user, id))

        return super(ShowcaseController, self).edit(id, data=data, errors=errors,
                                                    error_summary=error_summary)

    def _guess_package_type(self, expecting_name=False):
        """Showcase packages are always DATASET_TYPE_NAME."""

        return DATASET_TYPE_NAME

    def _save_new(self, context, package_type=None):
        '''
        The showcase is created then redirects to the manage_dataset page to
        associated packages with the new showcase.
        '''

        data_dict = clean_dict(dict_fns.unflatten(
                tuplize_dict(parse_params(request.POST))))

        data_dict['type'] = package_type
        context['message'] = data_dict.get('log_message', '')

        try:
            pkg_dict = get_action('ckanext_showcase_create')(context, data_dict)
        except ValidationError as e:
            errors = e.error_dict
            error_summary = e.error_summary
            data_dict['state'] = 'none'
            return self.new(data_dict, errors, error_summary)

        # redirect to add datasets
        url = h.url_for(controller='ckanext.showcase.controller:ShowcaseController',
                        action='add_datasets',
                        id=pkg_dict['name'])
        redirect(url)

    def _save_edit(self, name_or_id, context, package_type=None):
        '''
        Edit a showcase's details, then redirect to the showcase read page.
        '''

        data_dict = clean_dict(dict_fns.unflatten(
            tuplize_dict(parse_params(request.POST))))

        data_dict['id'] = name_or_id
        try:
            pkg = get_action('ckanext_showcase_update')(context, data_dict)
        except ValidationError as e:
            errors = e.error_dict
            error_summary = e.error_summary
            return self.edit(name_or_id, data_dict, errors, error_summary)

        c.pkg_dict = pkg

        # redirect to showcase details page
        url = h.url_for(controller='ckanext.showcase.controller:ShowcaseController',
                        action='read', id=pkg['name'])
        redirect(url)

    def read(self, id, format='html'):
        '''
        Detail view for a single showcase, listing its associated datasets.
        '''

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'for_view': True,
                   'auth_user_obj': c.userobj}
        data_dict = {'id': id}

        # check if showcase exists
        try:
            c.pkg_dict = get_action('package_show')(context, data_dict)
        except NotFound:
            abort(404, _('Showcase not found'))
        except NotAuthorized:
            abort(401, _('Unauthorized to read showcase %s') % id)

        # get showcase packages
        c.showcase_pkgs = get_action('ckanext_showcase_package_list')(context, {'showcase_id': c.pkg_dict['id']})

        package_type = DATASET_TYPE_NAME

        return render(self._read_template(package_type),
                      extra_vars={'dataset_type': package_type})

    def dataset_showcase_list(self, id):
        '''
        Display a list of showcases a dataset is associated with.
        '''
        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'for_view': True,
                   'auth_user_obj': c.userobj}
        data_dict = {'id': id}

        try:
            check_access('package_show', context, data_dict)
        except NotFound:
            abort(404, _('Dataset not found'))
        except NotAuthorized:
            abort(401, _('Not authorized to see this page'))

        try:
            c.pkg_dict = get_action('package_show')(context, data_dict)
            c.showcase_list = get_action('ckanext_package_showcase_list')(context, {'package_id': c.pkg_dict['id']})
        except NotFound:
            abort(404, _('Dataset not found'))
        except logic.NotAuthorized:
            abort(401, _('Unauthorized to read package %s') % id)

        return render("package/dataset_showcase_list.html")

    def manage_datasets(self, id):
        '''
        List datasets associated with the given showcase id.
        '''

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author}
        data_dict = {'id': id}

        try:
            check_access('ckanext_showcase_update', context)
        except NotAuthorized:
            abort(401, _('User %r not authorized to edit %s') % (c.user, id))

        # check if showcase exists
        try:
            c.pkg_dict = get_action('package_show')(context, data_dict)
        except NotFound:
            abort(404, _('Showcase not found'))
        except NotAuthorized:
            abort(401, _('Unauthorized to read showcase %s') % id)

        # Are we removing a showcase/dataset association?
        if request.method == 'POST' and 'bulk_action.showcase_remove' in request.params:
            # Find the datasets to perform the action on, they are prefixed by
            # dataset_ in the form data
            dataset_ids = []
            for param in request.params:
                if param.startswith('dataset_'):
                    dataset_ids.append(param[8:])
            if dataset_ids:
                for dataset_id in dataset_ids:
                    get_action('ckanext_showcase_package_association_delete')(context, {'showcase_id': c.pkg_dict['id'],
                                                                                        'package_id': dataset_id})
                h.flash_success(_("The dataset{plur} been removed from the showcase.".format(plur=" has" if len(dataset_ids) == 1 else "s have")))
                url = h.url_for(controller='ckanext.showcase.controller:ShowcaseController',
                                action='manage_datasets',
                                id=id)
                redirect(url)

        # get showcase packages
        c.showcase_pkgs = get_action('ckanext_showcase_package_list')(context, {'showcase_id': c.pkg_dict['id']})

        return render('showcase/manage_datasets.html')

    def add_datasets(self, id):
        '''
        Search for datasets and create associations with showcase.
        '''

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author}
        data_dict = {'id': id}

        try:
            check_access('ckanext_showcase_update', context)
        except NotAuthorized:
            abort(401, _('User %r not authorized to edit %s') % (c.user, id))

        # check if showcase exists
        try:
            c.pkg_dict = get_action('package_show')(context, data_dict)
        except NotFound:
            abort(404, _('Showcase not found'))
        except NotAuthorized:
            abort(401, _('Unauthorized to read showcase %s') % id)

        # Are we creating a showcase/dataset association?
        if request.method == 'POST' and 'bulk_action.showcase_add' in request.params:
            # Find the datasets to perform the action on, they are prefixed by
            # dataset_ in the form data
            dataset_ids = []
            for param in request.params:
                if param.startswith('dataset_'):
                    dataset_ids.append(param[8:])
            if dataset_ids:
                successful_adds = []
                for dataset_id in dataset_ids:
                    try:
                        get_action('ckanext_showcase_package_association_create')(context, {'showcase_id': c.pkg_dict['id'],
                                                                                            'package_id': dataset_id})
                    except ValidationError as e:
                        h.flash_notice(e.error_summary)
                    else:
                        successful_adds.append(dataset_id)
                if successful_adds:
                    h.flash_success(_("The dataset{plur} been added to the showcase.".format(plur=" has" if len(successful_adds) == 1 else "s have")))
                url = h.url_for(controller='ckanext.showcase.controller:ShowcaseController',
                                action='add_datasets',
                                id=id)
                redirect(url)

        self._add_dataset_search(c.pkg_dict['id'])

        return render('showcase/add_datasets.html')

    def _add_dataset_search(self, showcase_id):
        '''
        Search logic for discovering datasets to add to a showcase.
        '''

        from ckan.lib.search import SearchError

        package_type = 'dataset'

        # unicode format (decoded from utf8)
        q = c.q = request.params.get('q', u'')
        c.query_error = False
        page = self._get_page_number(request.params)

        limit = g.datasets_per_page

        # most search operations should reset the page counter:
        params_nopage = [(k, v) for k, v in request.params.items()
                         if k != 'page']

        def drill_down_url(alternative_url=None, **by):
            return h.add_url_param(alternative_url=alternative_url,
                                   controller='package', action='search',
                                   new_params=by)

        c.drill_down_url = drill_down_url

        def remove_field(key, value=None, replace=None):
            return h.remove_url_param(key, value=value, replace=replace,
                                      controller='package', action='search')

        c.remove_field = remove_field

        sort_by = request.params.get('sort', None)
        params_nosort = [(k, v) for k, v in params_nopage if k != 'sort']

        def _sort_by(fields):
            """
            Sort by the given list of fields.

            Each entry in the list is a 2-tuple: (fieldname, sort_order)

            eg - [('metadata_modified', 'desc'), ('name', 'asc')]

            If fields is empty, then the default ordering is used.
            """
            params = params_nosort[:]

            if fields:
                sort_string = ', '.join('%s %s' % f for f in fields)
                params.append(('sort', sort_string))
            return search_url(params, package_type)

        c.sort_by = _sort_by
        if sort_by is None:
            c.sort_by_fields = []
        else:
            c.sort_by_fields = [field.split()[0]
                                for field in sort_by.split(',')]

        def pager_url(q=None, page=None):
            params = list(params_nopage)
            params.append(('page', page))
            return search_url(params, package_type)

        c.search_url_params = urlencode(_encode_params(params_nopage))

        try:
            c.fields = []
            # c.fields_grouped will contain a dict of params containing
            # a list of values eg {'tags':['tag1', 'tag2']}
            c.fields_grouped = {}
            search_extras = {}
            fq = ''
            for (param, value) in request.params.items():
                if param not in ['q', 'page', 'sort'] \
                        and len(value) and not param.startswith('_'):
                    if not param.startswith('ext_'):
                        c.fields.append((param, value))
                        fq += ' %s:"%s"' % (param, value)
                        if param not in c.fields_grouped:
                            c.fields_grouped[param] = [value]
                        else:
                            c.fields_grouped[param].append(value)
                    else:
                        search_extras[param] = value

            context = {'model': model, 'session': model.Session,
                       'user': c.user or c.author, 'for_view': True,
                       'auth_user_obj': c.userobj}

            if package_type and package_type != 'dataset':
                # Only show datasets of this particular type
                fq += ' +dataset_type:{type}'.format(type=package_type)
            else:
                # Unless changed via config options, don't show non standard
                # dataset types on the default search page
                if not asbool(config.get('ckan.search.show_all_types', 'False')):
                    fq += ' +dataset_type:dataset'

            # Only search for packages that aren't already associated with the
            # Showcase
            associated_package_ids = ShowcasePackageAssociation.get_package_ids_for_showcase(showcase_id)
            # flatten resulting list to space separated string
            if associated_package_ids:
                associated_package_ids_str = ' OR '.join([id[0] for id in associated_package_ids])
                fq += ' !id:({0})'.format(associated_package_ids_str)

            facets = OrderedDict()

            default_facet_titles = {
                    'organization': _('Organizations'),
                    'groups': _('Groups'),
                    'tags': _('Tags'),
                    'res_format': _('Formats'),
                    'license_id': _('Licenses'),
                    }

            for facet in g.facets:
                if facet in default_facet_titles:
                    facets[facet] = default_facet_titles[facet]
                else:
                    facets[facet] = facet

            # Facet titles
            for plugin in p.PluginImplementations(p.IFacets):
                facets = plugin.dataset_facets(facets, package_type)

            c.facet_titles = facets

            data_dict = {
                'q': q,
                'fq': fq.strip(),
                'facet.field': facets.keys(),
                'rows': limit,
                'start': (page - 1) * limit,
                'sort': sort_by,
                'extras': search_extras
            }

            query = get_action('package_search')(context, data_dict)
            c.sort_by_selected = query['sort']

            c.page = h.Page(
                collection=query['results'],
                page=page,
                url=pager_url,
                item_count=query['count'],
                items_per_page=limit
            )
            c.facets = query['facets']
            c.search_facets = query['search_facets']
            c.page.items = query['results']
        except SearchError, se:
            log.error('Dataset search error: %r', se.args)
            c.query_error = True
            c.facets = {}
            c.search_facets = {}
            c.page = h.Page(collection=[])
        c.search_facets_limits = {}
        for facet in c.search_facets.keys():
            try:
                limit = int(request.params.get('_%s_limit' % facet,
                                               g.facets_default_number))
            except ValueError:
                abort(400, _('Parameter "{parameter_name}" is not '
                             'an integer').format(
                                 parameter_name='_%s_limit' % facet
                             ))
            c.search_facets_limits[facet] = limit
