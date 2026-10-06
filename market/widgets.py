from django import forms


class FlowerTagSelect(forms.CheckboxSelectMultiple):
    template_name = "market/widgets/flower_picker.html"
    option_template_name = "market/widgets/flower_option.html"

    def create_option(self, name, value, label, selected, index, **kwargs):
        option = super().create_option(name, value, label, selected, index, **kwargs)
        option["palette"] = value.instance.tag_palette
        return option
