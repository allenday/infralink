"""Generated public projection for declared fleet validation."""

from infralink.operator_surface import fleet_click_command as _fleet_click_command

# The public root owns --registry/--edges and output selection. This mounted
# command is generated from the one registered fleet.validate operation.
fleet = _fleet_click_command()
